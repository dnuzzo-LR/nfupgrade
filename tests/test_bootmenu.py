import errno
import os
import shutil
import signal
import tempfile
import threading
import time
import unittest
from unittest import mock
from nfupgrader import bootmenu


def mkproc(root, pid, ppid, argv, name="x", starttime=0):
    """Create a fake /proc/<pid> entry: stat (name may embed spaces/parens,
    ppid is field 4 after the LAST ')', starttime is field 22 i.e. index 19
    of the remainder split) and cmdline (NUL-separated)."""
    d = os.path.join(root, str(pid))
    os.makedirs(d)
    # remainder fields (after the name's closing paren), starting at field 3
    # (state): index 0 = state, index 1 = ppid, indices 2..18 = filler,
    # index 19 = starttime.
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(starttime)]
    with open(os.path.join(d, "stat"), "w") as fh:
        fh.write("{} ({}) {}\n".format(pid, name, " ".join(fields)))
    with open(os.path.join(d, "cmdline"), "wb") as fh:
        fh.write("\0".join(argv).encode("ascii") + b"\0")


class TestDiscovery(unittest.TestCase):
    SESSION_STARTTIME = 5000

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        # 10: ksh .../boot_nms ; 11: vi boot_nms (not a menu) ; 12: /usr/cnc/bin/new_boot_nms
        mkproc(self.root, 10, 12, ["/usr/bin/ksh", "/usr/cnc/bin/boot_nms"],
               starttime=self.SESSION_STARTTIME)
        mkproc(self.root, 11, 1, ["vi", "boot_nms"])
        mkproc(self.root, 12, 1, ["/usr/cnc/bin/new_boot_nms"],
               starttime=self.SESSION_STARTTIME)

    def _write_pidfile(self, path, tool_pid, session_pid, starttime):
        with open(path, "w") as fh:
            fh.write("{} {} {}\n".format(tool_pid, session_pid, starttime))

    def test_default_argv_never_a_shell(self):
        a = bootmenu.default_argv()
        self.assertEqual(a[:5], ["su", "cmm", "-s", "/bin/sh", "-c"])
        self.assertIn("exec /usr/cnc/bin/new_boot_nms", a[5])
        self.assertTrue(a[5].rstrip().endswith("exec /usr/cnc/bin/new_boot_nms"))

    def test_list_and_menu_detection(self):
        procs = bootmenu.list_processes(self.root)
        menus = [p["pid"] for p in procs if bootmenu.is_menu_argv(p["argv"])]
        self.assertEqual(sorted(menus), [10, 12])
        self.assertEqual(bootmenu.descendants(procs, 12), {10})  # 10's ppid is 12

    def test_list_processes_records_starttime(self):
        procs = bootmenu.list_processes(self.root)
        p12 = [p for p in procs if p["pid"] == 12][0]
        self.assertEqual(p12["starttime"], self.SESSION_STARTTIME)

    def test_list_processes_handles_name_with_parens_and_spaces(self):
        mkproc(self.root, 20, 1, ["weird"], name="my (odd) proc name")
        procs = bootmenu.list_processes(self.root)
        p20 = [p for p in procs if p["pid"] == 20][0]
        self.assertEqual(p20["ppid"], 1)

    def test_list_processes_skips_unreadable_or_vanished(self):
        # A pid directory with no stat/cmdline (simulating a vanished process).
        os.makedirs(os.path.join(self.root, "999"))
        procs = bootmenu.list_processes(self.root)
        self.assertNotIn(999, [p["pid"] for p in procs])
        # Non-numeric entries under /proc are ignored.
        os.makedirs(os.path.join(self.root, "not_a_pid"))
        procs = bootmenu.list_processes(self.root)
        self.assertEqual(sorted(p["pid"] for p in procs), [10, 11, 12])

    def test_classify(self):
        procs = bootmenu.list_processes(self.root)
        pf = os.path.join(self.root, "pidfile")
        self._write_pidfile(pf, 999, 12, self.SESSION_STARTTIME)

        self.assertEqual(
            bootmenu.classify_others(procs, {10, 12}, pf, lambda p: True),
            ("none", []))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: False),
            ("leftover", [10, 12]))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: True),
            ("foreign", [10, 12]))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), "/nonexistent", lambda p: False),
            ("foreign", [10, 12]))

    def test_classify_rejects_reused_session_pid(self):
        # Repro: old tool pid 500 is dead; the pidfile says session pid 12
        # started at starttime 1 (its old starttime). Pid 12 has since been
        # reused by an unrelated, live console login also running
        # new_boot_nms (starttime SESSION_STARTTIME per setUp, with child
        # 10). Without the starttime check this would wrongly read as
        # "leftover" and offer an "End it" button for a live login.
        procs = bootmenu.list_processes(self.root)
        pf = os.path.join(self.root, "pidfile")
        self._write_pidfile(pf, 500, 12, 1)  # recorded starttime (1) != actual
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: False),
            ("foreign", [10, 12]))

    def test_classify_matching_starttime_is_leftover(self):
        procs = bootmenu.list_processes(self.root)
        pf = os.path.join(self.root, "pidfile")
        self._write_pidfile(pf, 500, 12, self.SESSION_STARTTIME)
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: False),
            ("leftover", [10, 12]))

    def test_classify_starttime_mismatch_is_foreign(self):
        procs = bootmenu.list_processes(self.root)
        pf = os.path.join(self.root, "pidfile")
        self._write_pidfile(pf, 500, 12, self.SESSION_STARTTIME + 1)
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: False),
            ("foreign", [10, 12]))

    def test_classify_session_pid_gone_is_foreign(self):
        procs = bootmenu.list_processes(self.root)
        pf = os.path.join(self.root, "pidfile")
        self._write_pidfile(pf, 500, 9999, self.SESSION_STARTTIME)
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: False),
            ("foreign", [10, 12]))

    def test_classify_malformed_pidfiles_are_foreign(self):
        procs = bootmenu.list_processes(self.root)
        always_dead = lambda p: False

        two_field = os.path.join(self.root, "pf_two_field")
        with open(two_field, "w") as fh:
            fh.write("500 12\n")
        self.assertEqual(
            bootmenu.classify_others(procs, set(), two_field, always_dead),
            ("foreign", [10, 12]))

        one_field = os.path.join(self.root, "pf_one_field")
        with open(one_field, "w") as fh:
            fh.write("500\n")
        self.assertEqual(
            bootmenu.classify_others(procs, set(), one_field, always_dead),
            ("foreign", [10, 12]))

        non_integer = os.path.join(self.root, "pf_non_integer")
        with open(non_integer, "w") as fh:
            fh.write("abc 12 {}\n".format(self.SESSION_STARTTIME))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), non_integer, always_dead),
            ("foreign", [10, 12]))

        too_many = os.path.join(self.root, "pf_too_many")
        with open(too_many, "w") as fh:
            fh.write("500 12 {} extra\n".format(self.SESSION_STARTTIME))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), too_many, always_dead),
            ("foreign", [10, 12]))

    def test_write_and_read_pidfile_round_trip(self):
        pf = os.path.join(self.root, "pidfile")
        bootmenu.write_pidfile(pf, 42, 12, proc_root=self.root)
        self.assertEqual(
            bootmenu.read_pidfile(pf), (42, 12, self.SESSION_STARTTIME))

    def test_write_pidfile_session_pid_not_found_records_zero_starttime(self):
        pf = os.path.join(self.root, "pidfile")
        bootmenu.write_pidfile(pf, 42, 9999, proc_root=self.root)
        self.assertEqual(bootmenu.read_pidfile(pf), (42, 9999, 0))

    def test_pid_alive_default_treats_eperm_as_alive(self):
        with mock.patch("os.kill", side_effect=OSError(errno.EPERM, "perm")):
            self.assertTrue(bootmenu._pid_alive_default(1))

    def test_pid_alive_default_treats_esrch_as_dead(self):
        with mock.patch("os.kill", side_effect=OSError(errno.ESRCH, "no such")):
            self.assertFalse(bootmenu._pid_alive_default(999999))

    def test_pid_alive_default_true_when_kill_succeeds(self):
        with mock.patch("os.kill", return_value=None):
            self.assertTrue(bootmenu._pid_alive_default(1))


    def test_write_pidfile_is_atomic_leaves_no_tmp(self):
        pf = os.path.join(self.root, "pidfile")
        bootmenu.write_pidfile(pf, 42, 12, proc_root=self.root)
        self.assertFalse(os.path.exists(pf + ".tmp"))
        self.assertEqual(bootmenu.read_pidfile(pf), (42, 12, self.SESSION_STARTTIME))

    def test_proc_name(self):
        n = bootmenu.proc_name
        self.assertEqual(n(["su", "cmm", "-s", "/bin/sh", "-c", "x"]), "su")
        self.assertEqual(n(["/usr/bin/ksh", "/usr/cnc/bin/boot_nms"]), "boot_nms")
        self.assertEqual(n(["/bin/sh", "-x", "/usr/cnc/bin/new_boot_nms"]), "new_boot_nms")
        self.assertEqual(n(["sh", "-c", "/usr/cnc/bin/boot_nms"]), "sh")
        self.assertEqual(n(["/bin/bash"]), "bash")
        self.assertEqual(n(["/usr/bin/sleep", "30"]), "sleep")
        self.assertEqual(n([]), "")


def wait_for(cond, timeout=5.0):
    """Poll cond() until it is truthy or timeout seconds pass; return its
    last value."""
    deadline = time.time() + timeout
    while True:
        v = cond()
        if v or time.time() >= deadline:
            return v
        time.sleep(0.02)


def _read(path):
    """Contents of path, or "" if it can't be read (yet)."""
    try:
        with open(path) as fh:
            return fh.read()
    except (IOError, OSError):
        return ""


def _kill_quietly(pid):
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


class Collector(object):
    """A send() callable that records frames."""

    def __init__(self):
        self.frames = []
        self.lock = threading.Lock()

    def __call__(self, frame):
        with self.lock:
            self.frames.append(frame)

    def snapshot(self):
        with self.lock:
            return list(self.frames)

    def output(self, include_replay=True):
        return "".join(f["data"] for f in self.snapshot()
                       if f["type"] == "output" and (include_replay or not f.get("replay")))

    def types(self):
        return [f["type"] for f in self.snapshot()]


class TestSession(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.pidfile = os.path.join(self.dir, "bootmenu.pid")

    def session(self, argv, **kw):
        kw.setdefault("pidfile", self.pidfile)
        s = bootmenu.BootMenuSession(argv=argv, **kw)
        self.addCleanup(self._reap, s)
        return s

    def _reap(self, s):
        """Kill everything the session still owns and wait for it to end."""
        if s.running():
            for pid in s.own_pids():
                _kill_quietly(pid)
            wait_for(lambda: not s.running())
        self.assertFalse(s.running(), "session did not end in cleanup")

    def test_on_exit_natural_exit_called_once(self):
        calls = []
        s = self.session(["/bin/sh", "-c", "exit 3"], on_exit=calls.append)
        s.start()
        self.assertTrue(wait_for(lambda: len(calls) == 1), calls)
        self.assertEqual(calls, [{"exit_code": 3, "forced": False, "reason": None}])
        self.assertEqual(s.state()["state"], "ended")

    def test_on_exit_forced_end_records_reason(self):
        calls = []
        s = self.session(["cat"], kill_names={"cat"})
        s.on_exit = calls.append
        s.start()
        self.assertTrue(s.end(reason="tool_stop"))
        # end() returns only after on_exit has run
        self.assertEqual(calls, [{"exit_code": 128 + signal.SIGKILL, "forced": True,
                                  "reason": "tool_stop"}])
        # the next session starts clean: a natural exit is not "forced"
        s.argv = ["/bin/sh", "-c", "exit 0"]
        s.start()
        self.assertTrue(wait_for(lambda: len(calls) == 2), calls)
        self.assertEqual(calls[1], {"exit_code": 0, "forced": False, "reason": None})

    def test_on_exit_end_without_reason(self):
        calls = []
        s = self.session(["cat"], kill_names={"cat"}, on_exit=calls.append)
        s.start()
        self.assertTrue(s.end())
        self.assertEqual(calls, [{"exit_code": 128 + signal.SIGKILL, "forced": True,
                                  "reason": None}])

    def test_on_exit_raising_does_not_break_finalisation(self):
        def boom(info):
            raise RuntimeError("audit failed")
        s = self.session(["cat"], kill_names={"cat"}, on_exit=boom)
        c = Collector()
        s.attach(c)
        s.start()
        self.assertTrue(s.end())
        self.assertEqual(s.state()["state"], "ended")
        self.assertFalse(os.path.exists(self.pidfile))
        self.assertTrue(wait_for(lambda: "exit" in c.types()), c.types())

    def test_state_none_before_start(self):
        s = self.session(["/bin/sh", "-c", "exit 0"])
        self.assertEqual(s.state(), {"state": "none", "pid": None,
                                     "started_at": None, "exit_code": None})
        self.assertFalse(s.running())
        self.assertEqual(s.own_pids(), set())
        self.assertFalse(s.busy())
        self.assertFalse(s.end())

    def test_runs_on_a_tty(self):
        s = self.session(["/bin/sh", "-c", "tty; read a; echo got:$a"])
        c = Collector()
        s.attach(c)
        pid = s.start()
        self.assertGreater(pid, 0)
        st = s.state()
        self.assertEqual(st["state"], "running")
        self.assertEqual(st["pid"], pid)
        self.assertIsNotNone(st["started_at"])
        self.assertTrue(wait_for(lambda: "/dev/pts/" in c.output()), c.output())
        s.write("hello\r")
        self.assertTrue(wait_for(lambda: "got:hello" in c.output()), c.output())
        self.assertTrue(wait_for(lambda: s.state()["state"] == "ended"))
        self.assertEqual(s.state()["exit_code"], 0)
        self.assertTrue(wait_for(lambda: "exit" in c.types()))
        self.assertIn({"type": "exit", "code": 0}, c.snapshot())

    def test_start_twice_raises(self):
        s = self.session(["cat"], kill_names={"cat"})
        s.start()
        with self.assertRaises(RuntimeError):
            s.start()

    def test_env_has_term(self):
        s = self.session(["/bin/sh", "-c", "echo T=$TERM"])
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        self.assertIn("T=xterm-256color", s.scrollback())

    def test_size_is_set(self):
        s = self.session(["/bin/sh", "-c", "stty size"])
        s.start(cols=100, rows=30)
        self.assertTrue(wait_for(lambda: not s.running()))
        self.assertIn("30 100", s.scrollback())

    def test_resize(self):
        s = self.session(["/bin/sh", "-c", "read a; stty size; read b"])
        s.start()
        s.resize(120, 40)
        s.write("x\r")
        self.assertTrue(wait_for(lambda: "40 120" in s.scrollback()), s.scrollback())
        s.write("y\r")
        self.assertTrue(wait_for(lambda: not s.running()))

    def test_menu_exits_immediately(self):
        s = self.session(["/bin/sh", "-c", "echo ERROR: PLEASE CHECK; exit 1"])
        s.start()
        self.assertTrue(wait_for(lambda: s.state()["state"] == "ended"))
        self.assertEqual(s.state()["exit_code"], 1)
        self.assertIn("ERROR: PLEASE CHECK", s.scrollback())
        self.assertFalse(s.running())
        self.assertFalse(os.path.exists(self.pidfile))

    def test_attach_after_end_gets_replay_and_status(self):
        s = self.session(["/bin/sh", "-c", "echo bye; exit 3"])
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        c = Collector()
        s.attach(c)
        f = c.snapshot()
        self.assertEqual(f[0]["type"], "output")
        self.assertTrue(f[0]["replay"])
        self.assertIn("bye", f[0]["data"])
        self.assertEqual(f[1], {"type": "status", "state": "ended", "exit_code": 3})

    def test_second_attach_takes_over(self):
        s = self.session(["cat"], kill_names={"cat"})
        s.start()
        s.write("first\r")
        self.assertTrue(wait_for(lambda: "first" in s.scrollback()))
        a, b = Collector(), Collector()
        s.attach(a)
        self.assertEqual(a.snapshot()[-1], {"type": "status", "state": "running"})
        s.attach(b)
        self.assertEqual(a.snapshot()[-1], {"type": "detached", "reason": "opened elsewhere"})
        bf = b.snapshot()
        self.assertEqual(bf[0]["type"], "output")
        self.assertTrue(bf[0]["replay"])
        self.assertIn("first", bf[0]["data"])
        self.assertEqual(bf[1], {"type": "status", "state": "running"})
        a_count = len(a.snapshot())
        s.write("x\r")
        self.assertTrue(wait_for(lambda: "x" in b.output(include_replay=False)))
        self.assertEqual(len(a.snapshot()), a_count)
        self.assertTrue(s.end())
        self.assertTrue(wait_for(lambda: "exit" in b.types()))
        self.assertNotIn("exit", a.types())

    def test_detach_stops_output_and_stale_detach_is_noop(self):
        s = self.session(["cat"], kill_names={"cat"})
        s.start()
        a, b = Collector(), Collector()
        ta = s.attach(a)
        tb = s.attach(b)
        s.detach(ta)  # stale token: must not detach b
        s.write("one\r")
        self.assertTrue(wait_for(lambda: "one" in b.output()))
        s.detach(tb)
        n = len(b.snapshot())
        s.write("two\r")
        self.assertTrue(wait_for(lambda: "two" in s.scrollback()))
        self.assertEqual(len(b.snapshot()), n)

    def test_send_error_detaches(self):
        s = self.session(["cat"], kill_names={"cat"})
        s.start()
        calls = []

        def bad(frame):
            calls.append(frame)
            if frame["type"] == "output":
                raise IOError("socket gone")

        s.attach(bad)
        s.write("boom\r")
        self.assertTrue(wait_for(lambda: "boom" in s.scrollback()))
        self.assertTrue(wait_for(lambda: any(f["type"] == "output" for f in calls)))
        n = len(calls)
        s.write("more\r")
        self.assertTrue(wait_for(lambda: "more" in s.scrollback()))
        self.assertEqual(len(calls), n)
        self.assertTrue(s.running())

    def test_scrollback_capped(self):
        s = self.session(["/bin/sh", "-c",
                          "i=0; while [ $i -lt 100 ]; do printf '%049d\\n' $i; i=$((i+1)); done; echo END"],
                         scrollback=1000)
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        sb = s.scrollback()
        self.assertLessEqual(len(sb), 1000)
        self.assertTrue(sb.rstrip().endswith("END"), sb[-80:])
        self.assertIn("0000099", sb)

    def test_end_kills_only_menu_processes(self):
        menu_sh = os.path.join(self.dir, "menu.sh")
        pidf = os.path.join(self.dir, "bg.pid")
        with open(menu_sh, "w") as fh:
            # nohup: a process the menu started must outlive the session; a
            # plain `&` child is SIGHUPed by the kernel when the session
            # leader dies, whatever we do.
            fh.write("#!/bin/sh\nnohup sleep 300 >/dev/null 2>&1 &\necho $! > \"$1\"\n"
                     "while :; do sleep 1; done\n")
        s = self.session(["/bin/sh", menu_sh, pidf], kill_names={"menu.sh"})
        s.start()
        self.assertTrue(wait_for(lambda: _read(pidf).strip()))
        bg = int(_read(pidf))
        self.addCleanup(_kill_quietly, bg)
        self.assertTrue(wait_for(lambda: bg in s.own_pids()))
        self.assertTrue(wait_for(s.busy))
        self.assertTrue(s.end())
        self.assertEqual(s.state()["state"], "ended")
        self.assertEqual(s.state()["exit_code"], 128 + signal.SIGKILL)
        os.kill(bg, 0)  # still alive: raises if end() killed it
        with open("/proc/{}/cmdline".format(bg), "rb") as fh:
            self.assertIn(b"sleep", fh.read())
        self.assertFalse(os.path.exists(self.pidfile))

    def test_idle_menu_not_busy(self):
        menu_sh = os.path.join(self.dir, "menu.sh")
        with open(menu_sh, "w") as fh:
            fh.write("#!/bin/sh\nread a\n")
        s = self.session(["/bin/sh", menu_sh], kill_names={"menu.sh"})
        s.start()
        self.assertTrue(wait_for(lambda: s.own_pids()))
        self.assertFalse(s.busy())
        self.assertTrue(s.end())
        self.assertFalse(s.running())

    def test_end_ignores_names_not_listed(self):
        s = self.session(["cat"], kill_names={"menu.sh"})
        s.start()
        self.assertFalse(s.end())
        self.assertTrue(s.running())

    def test_pidfile(self):
        s = self.session(["cat"], kill_names={"cat"})
        pid = s.start()
        tool_pid, session_pid, starttime = bootmenu.read_pidfile(self.pidfile)
        self.assertEqual((tool_pid, session_pid), (os.getpid(), pid))
        self.assertGreater(starttime, 0)
        with open(self.pidfile) as fh:
            self.assertEqual(fh.read().split()[:2], [str(os.getpid()), str(pid)])
        self.assertTrue(s.end())
        self.assertTrue(wait_for(lambda: not os.path.exists(self.pidfile)))

    def test_write_when_not_running_raises(self):
        s = self.session(["/bin/sh", "-c", "exit 0"])
        with self.assertRaises(RuntimeError):
            s.write("x")
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        with self.assertRaises(RuntimeError):
            s.write("x")

    def test_exec_failure_ends_with_127(self):
        s = self.session(["/nonexistent/menu"])
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        self.assertEqual(s.state()["exit_code"], 127)
        self.assertIn("exec /nonexistent/menu failed", s.scrollback())

    def test_restart_after_end(self):
        s = self.session(["/bin/sh", "-c", "echo run"])
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        s.start()
        self.assertTrue(wait_for(lambda: not s.running()))
        self.assertEqual(s.state()["exit_code"], 0)

    def test_defaults(self):
        s = bootmenu.BootMenuSession()
        self.assertEqual(s.argv, bootmenu.default_argv())
        self.assertEqual(s.kill_names, {"su", "new_boot_nms", "boot_nms"})
        self.assertEqual(s.env["TERM"], "xterm-256color")


if __name__ == "__main__":
    unittest.main()

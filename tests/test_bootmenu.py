import errno
import os
import shutil
import tempfile
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


if __name__ == "__main__":
    unittest.main()

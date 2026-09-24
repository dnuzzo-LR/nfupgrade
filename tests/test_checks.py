import os
import stat
import subprocess
import sys
import tempfile
import unittest
from nfupgrader import checks
from nfupgrader.host import FakeHost, RunResult


class RecordingHost(object):
    """Maps tuple(argv) -> RunResult (default rc 0) and records (argv, timeout)."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def run(self, argv, timeout=None):
        self.calls.append((list(argv), timeout))
        return self.responses.get(tuple(argv), RunResult(0, "", ""))


class TestKinds(unittest.TestCase):
    def test_rpm_installed(self):
        h = FakeHost({("rpm", "-q", "ksh"): RunResult(0, "ksh-20120801\n", "")})
        ok, _ = checks.KINDS["rpm_installed"](h, {"pkg": "ksh"})
        self.assertTrue(ok)

    def test_rpm_missing(self):
        h = FakeHost({("rpm", "-q", "nope"): RunResult(1, "not installed\n", "")})
        ok, _ = checks.KINDS["rpm_installed"](h, {"pkg": "nope"})
        self.assertFalse(ok)

    def test_file_exists_and_absent(self):
        h = FakeHost({("test", "-f", "/a"): RunResult(0, "", ""),
                      ("test", "-f", "/b"): RunResult(1, "", "")})
        self.assertTrue(checks.KINDS["file_exists"](h, {"path": "/a"})[0])
        self.assertFalse(checks.KINDS["file_exists"](h, {"path": "/b"})[0])
        self.assertTrue(checks.KINDS["file_absent"](h, {"path": "/b"})[0])
        self.assertFalse(checks.KINDS["file_absent"](h, {"path": "/a"})[0])

    def test_symlink_target_contains(self):
        h = FakeHost({("readlink", "/usr/cnc"): RunResult(0, "/usr4/inc5.5.003\n", "")})
        self.assertTrue(checks.KINDS["symlink_target"](h, {"path": "/usr/cnc", "contains": "inc5.5"})[0])
        self.assertFalse(checks.KINDS["symlink_target"](h, {"path": "/usr/cnc", "contains": "inc9"})[0])

    def test_command_status(self):
        h = FakeHost({("true",): RunResult(0, "", ""), ("false",): RunResult(1, "", "")})
        self.assertTrue(checks.KINDS["command_status"](h, {"argv": ["true"]})[0])
        self.assertFalse(checks.KINDS["command_status"](h, {"argv": ["false"]})[0])
        self.assertTrue(checks.KINDS["command_status"](h, {"argv": ["false"], "expect_rc": 1})[0])

    def test_command_output_match_and_negate(self):
        h = FakeHost({("cat", "/usr/cnc/.GR_STATUS"): RunResult(0, "COMPLETED\n", "")})
        self.assertTrue(checks.KINDS["command_output_match"](
            h, {"argv": ["cat", "/usr/cnc/.GR_STATUS"], "pattern": "COMPLETED"})[0])
        self.assertTrue(checks.KINDS["command_output_match"](
            h, {"argv": ["cat", "/usr/cnc/.GR_STATUS"], "pattern": "IN_PROGRESS", "negate": True})[0])

    def test_disk_space_min(self):
        df = "H\n/d 100 1 6291456 5% /usr4\n"   # ~6 GiB free (kb)
        h = FakeHost({("df", "-kP"): RunResult(0, df, "")})
        self.assertTrue(checks.KINDS["disk_space_min"](h, {"mount": "/usr4", "min_gb": 5})[0])
        self.assertFalse(checks.KINDS["disk_space_min"](h, {"mount": "/usr4", "min_gb": 10})[0])

    def test_service_active(self):
        h = FakeHost({("systemctl", "is-active", "httpd"): RunResult(0, "active\n", "")})
        self.assertTrue(checks.KINDS["service_active"](h, {"name": "httpd"})[0])

    def test_dbcheck_clean(self):
        h = FakeHost({("dbcheck", "-AV"): RunResult(0, "all good\n", "")})
        self.assertTrue(checks.KINDS["dbcheck_clean"](h, {})[0])
        h2 = FakeHost({("dbcheck", "-AV"): RunResult(0, "ERROR: x\n", "")})
        self.assertFalse(checks.KINDS["dbcheck_clean"](h2, {})[0])

    def test_staged_present(self):
        h = FakeHost({("readlink", "/usr/cnc_stage"): RunResult(0, "/usr4/inc5.5.103\n", "")})
        self.assertTrue(checks.KINDS["staged_present"](h, {})[0])
        h2 = FakeHost({("readlink", "/usr/cnc_stage"): RunResult(1, "", "")})
        self.assertFalse(checks.KINDS["staged_present"](h2, {})[0])

    def test_touchfile(self):
        h = FakeHost({("test", "-f", "/usr/cnc/features/ems"): RunResult(0, "", "")})
        self.assertTrue(checks.KINDS["touchfile"](h, {"file": "ems"})[0])
        h2 = FakeHost({("test", "-f", "/usr/cnc/features/nms"): RunResult(1, "", "")})
        self.assertFalse(checks.KINDS["touchfile"](h2, {"file": "nms"})[0])

    def test_system_define_membership(self):
        cmd = ("sh", "-c", "grep -E '^M_SDI_TEST_MODE=' /usr/cnc/features/system_defines 2>/dev/null | tail -1")
        h = FakeHost({cmd: RunResult(0, "M_SDI_TEST_MODE=1\n", "")})
        self.assertTrue(checks.KINDS["system_define"](h, {"variable": "M_SDI_TEST_MODE", "expected_values": ["1"]})[0])
        self.assertFalse(checks.KINDS["system_define"](h, {"variable": "M_SDI_TEST_MODE", "expected_values": ["0"]})[0])

    def test_system_define_absent_matches_empty_expected(self):
        cmd = ("sh", "-c", "grep -E '^INC_API_LIBCURL=' /usr/cnc/features/system_defines 2>/dev/null | tail -1")
        h = FakeHost({cmd: RunResult(1, "", "")})   # not present -> value ""
        self.assertTrue(checks.KINDS["system_define"](h, {"variable": "INC_API_LIBCURL", "expected_values": ["", "0"]})[0])

    def test_kernel_param(self):
        h = FakeHost({("sysctl", "kernel.pid_max"): RunResult(0, "kernel.pid_max = 65000\n", "")})
        self.assertTrue(checks.KINDS["kernel_param"](h, {"param": "kernel.pid_max", "expected_value": "65000"})[0])
        self.assertFalse(checks.KINDS["kernel_param"](h, {"param": "kernel.pid_max", "expected_value": "99999"})[0])

    def test_kernel_param_whitespace_insensitive(self):
        h = FakeHost({("sysctl", "net.ipv4.tcp_rmem"):
                      RunResult(0, "net.ipv4.tcp_rmem = 4096\t87380\t6291456\n", "")})
        self.assertTrue(checks.KINDS["kernel_param"](
            h, {"param": "net.ipv4.tcp_rmem", "expected_value": "4096 87380  6291456"})[0])

    def test_touchfile_rejects_path_names(self):
        for bad in ("a/b", "../ems", ".", "..", "/etc/x"):
            h = RecordingHost()
            self.assertEqual(checks.KINDS["touchfile"](h, {"file": bad}),
                             (False, "invalid touchfile name"), bad)
            self.assertEqual(h.calls, [])

    def test_ulimit(self):
        d = tempfile.mkdtemp()
        saved = (checks.LIMITS_MAIN, checks.LIMITS_DIR)
        checks.LIMITS_MAIN = os.path.join(d, "limits.conf")
        checks.LIMITS_DIR = os.path.join(d, "limits.d")
        try:
            os.makedirs(checks.LIMITS_DIR)
            open(checks.LIMITS_MAIN, "w").write("# comment\ncmm soft nproc 1024\n")
            open(os.path.join(checks.LIMITS_DIR, "90-a.conf"), "w").write(
                "cmm soft nproc 32768\ncmm hard nproc 32768\n")
            open(os.path.join(checks.LIMITS_DIR, "ignored.txt"), "w").write("cmm hard nproc 1\n")
            h = FakeHost()
            self.assertEqual(checks.KINDS["ulimit"](h, {"domain": "cmm", "limit_type": "soft",
                                                        "limit_item": "nproc",
                                                        "expected_value": "32768"}),
                             (True, "cmm nproc = 32768"))
            self.assertFalse(checks.KINDS["ulimit"](h, {"domain": "cmm", "limit_type": "hard",
                                                        "limit_item": "nproc",
                                                        "expected_value": "999"})[0])
            self.assertFalse(checks.KINDS["ulimit"](h, {"domain": "x", "limit_type": "hard",
                                                        "limit_item": "nproc",
                                                        "expected_value": "1"})[0])
        finally:
            checks.LIMITS_MAIN, checks.LIMITS_DIR = saved


class TestRun(unittest.TestCase):
    def _checks(self):
        return [
            {"name": "staged", "description": "staged load present", "fail_text": "ERROR",
             "when": "preflight", "phase": "upgrade", "kind": "staged_present", "params": {}},
            {"name": "disk", "description": "5GB on /usr4", "fail_text": "ERROR",
             "when": "preflight", "phase": "any", "kind": "disk_space_min",
             "params": {"mount": "/usr4", "min_gb": 5}},
            {"name": "warnonly", "description": "advisory", "fail_text": "WARNING",
             "when": "preflight", "phase": "stage", "kind": "file_exists",
             "params": {"path": "/nope"}},
            {"name": "post", "description": "dbcheck", "fail_text": "WARNING",
             "when": "postflight", "phase": "any", "kind": "dbcheck_clean", "params": {}},
            {"name": "bep-only", "description": "bep thing", "fail_text": "ERROR",
             "when": "preflight", "phase": "any", "machtype": "BEP",
             "kind": "file_exists", "params": {"path": "/x"}},
        ]

    def _host(self):
        return FakeHost({
            ("readlink", "/usr/cnc_stage"): RunResult(0, "/usr4/inc5.5.103\n", ""),
            ("df", "-kP"): RunResult(0, "H\n/d 1 1 1048576 99% /usr4\n", ""),  # 1 GiB free
            ("test", "-f", "/nope"): RunResult(1, "", ""),
            ("dbcheck", "-AV"): RunResult(0, "clean\n", ""),
        })

    def test_load_checks(self):
        p = os.path.join(tempfile.mkdtemp(), "c.json")
        json_text = '[{"name":"x","kind":"file_exists","when":"preflight","phase":"any","params":{"path":"/a"}}]'
        open(p, "w").write(json_text)
        self.assertEqual(len(checks.load_checks(p)), 1)

    def test_run_filters_by_when_phase_machtype(self):
        results = checks.run_checks(self._host(), self._checks(), "preflight", "upgrade", machtype="FEP")
        names = [r["name"] for r in results]
        self.assertIn("staged", names)      # preflight + upgrade
        self.assertIn("disk", names)        # preflight + any
        self.assertNotIn("warnonly", names) # phase stage != upgrade
        self.assertNotIn("post", names)     # postflight
        self.assertNotIn("bep-only", names) # machtype BEP != FEP

    def test_blocking_only_preflight_error_failures(self):
        results = checks.run_checks(self._host(), self._checks(), "preflight", "upgrade", machtype="FEP")
        blk = checks.blocking_failures(results)
        blk_names = [r["name"] for r in blk]
        self.assertIn("disk", blk_names)        # ERROR + failed (1GB < 5GB)
        self.assertNotIn("staged", blk_names)   # passed
        stage_res = checks.run_checks(self._host(), self._checks(), "preflight", "stage")
        stage_blk = [r["name"] for r in checks.blocking_failures(stage_res)]
        self.assertNotIn("warnonly", stage_blk)   # a WARNING failure never blocks
        self.assertIn("disk", stage_blk)          # ERROR failure (phase any) blocks in stage too

    def test_shipped_default_checks_load_and_use_known_kinds(self):
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "nfupgrader", "checks.default.json")
        loaded = checks.load_checks(path)
        self.assertGreaterEqual(len(loaded), 50)   # ported nf-install system checks
        for c in loaded:
            self.assertIn(c["kind"], checks.KINDS, "unknown kind: %s" % c["kind"])
            self.assertIn(c.get("when"), ("preflight", "postflight"))

    def test_result_shape(self):
        r = checks.run_checks(self._host(), self._checks(), "preflight", "upgrade", machtype="FEP")[0]
        for k in ("name", "description", "severity", "passed", "detail", "blocking", "fix"):
            self.assertIn(k, r)


class TestFixes(unittest.TestCase):
    NAMES = ("SYSCTL_CONF", "SYSCTL_DIRS", "SYSCTL_MAIN", "LIMITS_CONF", "LIMITS_MAIN",
             "LIMITS_DIR", "DNF")

    def setUp(self):
        # Every path a fix reads or writes points into a temp tree: nothing under /etc.
        self.d = d = tempfile.mkdtemp()
        self._saved = dict((n, getattr(checks, n)) for n in self.NAMES)
        checks.SYSCTL_DIRS = (os.path.join(d, "etc", "sysctl.d"), os.path.join(d, "run", "sysctl.d"),
                              os.path.join(d, "usr", "lib", "sysctl.d"))
        checks.SYSCTL_CONF = os.path.join(checks.SYSCTL_DIRS[0], "99-netflex.conf")
        checks.SYSCTL_MAIN = os.path.join(d, "etc", "sysctl.conf")
        checks.LIMITS_MAIN = os.path.join(d, "limits.conf")
        checks.LIMITS_DIR = os.path.join(d, "limits.d")
        checks.LIMITS_CONF = os.path.join(checks.LIMITS_DIR, "99-netflex.conf")
        checks.DNF = os.path.join(d, "dnf")
        open(checks.DNF, "w").close()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(checks, n, v)

    def write(self, path, text, mode="w"):
        dname = os.path.dirname(path)
        if not os.path.isdir(dname):
            os.makedirs(dname)
        with open(path, mode) as fh:
            fh.write(text)

    def check(self, kind, **params):
        return {"name": "c", "kind": kind, "when": "preflight", "phase": "any",
                "fail_text": "ERROR", "params": params}

    def test_describe_only_for_fixable_kinds(self):
        self.assertEqual(checks.describe_fix(self.check("rpm_installed", pkg="ksh")),
                         "dnf install -y ksh")
        self.assertIn("systemctl start firewalld",
                      checks.describe_fix(self.check("service_active", name="firewalld")))
        self.assertIn("sysctl -w kernel.shmmax=68719476736", checks.describe_fix(
            self.check("kernel_param", param="kernel.shmmax", expected_value="68719476736")))
        self.assertIn("/usr/cnc/features/ems",
                      checks.describe_fix(self.check("touchfile", file="ems")))
        self.assertIsNone(checks.describe_fix(self.check("system_define", variable="X")))
        self.assertIsNone(checks.describe_fix(self.check("dbcheck_clean")))

    def test_rpm_fix(self):
        h = RecordingHost()
        ok, detail = checks.apply_fix(h, self.check("rpm_installed", pkg="ksh"))
        self.assertTrue(ok)
        self.assertEqual(h.calls, [(["dnf", "install", "-y", "ksh"], 600)])
        h = RecordingHost({("dnf", "install", "-y", "nope"):
                           RunResult(1, "", "Last metadata...\nError: Unable to find a match: nope\n")})
        ok, detail = checks.apply_fix(h, self.check("rpm_installed", pkg="nope"))
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("rc=1: "))
        self.assertIn("Unable to find a match: nope", detail)

    def test_service_fix_start_and_enable(self):
        h = RecordingHost()
        ok, detail = checks.apply_fix(h, self.check("service_active", name="firewalld"))
        self.assertTrue(ok)
        self.assertEqual([c[0] for c in h.calls], [["systemctl", "start", "firewalld"],
                                                   ["systemctl", "enable", "firewalld"]])
        h = RecordingHost({("systemctl", "enable", "firewalld"): RunResult(1, "", "no")})
        ok, detail = checks.apply_fix(h, self.check("service_active", name="firewalld"))
        self.assertTrue(ok)
        self.assertTrue(detail.startswith("WARNING:"))
        self.assertEqual([c[1] for c in h.calls], [90, 30])   # start may be slow
        h = RecordingHost({("systemctl", "start", "firewalld"): RunResult(5, "", "boom")})
        ok, _ = checks.apply_fix(h, self.check("service_active", name="firewalld"))
        self.assertFalse(ok)
        self.assertEqual(len(h.calls), 1)

    def test_kernel_param_sets_and_persists(self):
        os.makedirs(os.path.dirname(checks.SYSCTL_CONF))
        open(checks.SYSCTL_CONF, "w").write("# ours\nkernel.shmmax = 1\nvm.swappiness=10\n")
        h = RecordingHost()
        ok, detail = checks.apply_fix(h, self.check("kernel_param", param="kernel.shmmax",
                                                    expected_value="68719476736"))
        self.assertTrue(ok)
        self.assertEqual(h.calls, [(["sysctl", "-w", "kernel.shmmax=68719476736"], 10)])
        self.assertEqual(open(checks.SYSCTL_CONF).read(),
                         "# ours\nvm.swappiness=10\nkernel.shmmax=68719476736\n")

    def test_kernel_param_creates_file_and_fails_on_sysctl_error(self):
        ok, _ = checks.apply_fix(RecordingHost(), self.check("kernel_param", param="a.b",
                                                             expected_value="1"))
        self.assertTrue(ok)
        self.assertEqual(open(checks.SYSCTL_CONF).read(), "a.b=1\n")
        h = RecordingHost({("sysctl", "-w", "a.b=2"): RunResult(255, "", "permission denied")})
        ok, detail = checks.apply_fix(h, self.check("kernel_param", param="a.b", expected_value="2"))
        self.assertFalse(ok)
        self.assertEqual(open(checks.SYSCTL_CONF).read(), "a.b=1\n")   # unchanged

    def test_ulimit_replaces_matching_entry(self):
        os.makedirs(os.path.dirname(checks.LIMITS_CONF))
        open(checks.LIMITS_CONF, "w").write("cnc soft nofile 1024\ncnc hard nofile 4096\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.check(
            "ulimit", domain="cnc", limit_type="soft", limit_item="nofile", expected_value="65536"))
        self.assertTrue(ok)
        self.assertEqual(open(checks.LIMITS_CONF).read(),
                         "cnc hard nofile 4096\ncnc soft nofile 65536\n")

    # --- rpm tool (RHEL 7 has no dnf) ---

    def test_rpm_fix_uses_yum_without_dnf(self):
        checks.DNF = os.path.join(self.d, "no-such-dnf")
        c = self.check("rpm_installed", pkg="ksh")
        self.assertEqual(checks.describe_fix(c), "yum install -y ksh")
        h = RecordingHost()
        self.assertTrue(checks.apply_fix(h, c)[0])
        self.assertEqual(h.calls, [(["yum", "install", "-y", "ksh"], 600)])

    # --- bad names ---

    def test_bad_names_have_no_fix_and_are_refused(self):
        bad = [self.check("rpm_installed", pkg="-y"), self.check("rpm_installed", pkg="--x"),
               self.check("service_active", name="--now"),
               self.check("touchfile", file="../ems"), self.check("touchfile", file="a/b"),
               self.check("touchfile", file="."), self.check("touchfile", file="..")]
        for c in bad:
            self.assertIsNone(checks.describe_fix(c), c)
            h = RecordingHost()
            ok, detail = checks.apply_fix(h, c)
            self.assertFalse(ok, c)
            self.assertEqual(h.calls, [], c)
            self.assertIn("invalid", detail)

    # --- _replace_lines robustness ---

    def test_non_utf8_bytes_preserved_under_c_locale(self):
        raw = b"# caf\xe9 \xc3\xa9 \xff\r\nkernel.shmmax = 1\nvm.x=1\n"
        self.write(checks.SYSCTL_CONF, raw, "wb")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from nfupgrader import checks; "
                "checks._replace_lines(sys.argv[2], "
                "lambda ln: not ln.startswith('kernel.shmmax'), 'kernel.shmmax=2')")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, LC_ALL="C", LANG="C")
        env.pop("PYTHONIOENCODING", None)
        r = subprocess.run([sys.executable, "-c", code, root, checks.SYSCTL_CONF], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(open(checks.SYSCTL_CONF, "rb").read(),
                         b"# caf\xe9 \xc3\xa9 \xff\r\nvm.x=1\nkernel.shmmax=2\n")

    def test_utf8_comment_preserved_in_process(self):
        raw = "# r\u00e9glage \u2014 ok\ncnc soft nofile 1\n".encode("utf-8")
        self.write(checks.LIMITS_CONF, raw, "wb")
        ok, _ = checks.apply_fix(RecordingHost(), self.check(
            "ulimit", domain="cnc", limit_type="soft", limit_item="nofile", expected_value="2"))
        self.assertTrue(ok)
        self.assertEqual(open(checks.LIMITS_CONF, "rb").read(),
                         "# r\u00e9glage \u2014 ok\ncnc soft nofile 2\n".encode("utf-8"))

    def test_mode_preserved(self):
        self.write(checks.SYSCTL_CONF, "a.b=0\n")
        os.chmod(checks.SYSCTL_CONF, 0o600)
        ok, _ = checks.apply_fix(RecordingHost(), self.check("kernel_param", param="a.b",
                                                             expected_value="1"))
        self.assertTrue(ok)
        self.assertEqual(stat.S_IMODE(os.stat(checks.SYSCTL_CONF).st_mode), 0o600)
        self.assertEqual(open(checks.SYSCTL_CONF).read(), "a.b=1\n")

    def test_symlink_target_updated_and_link_kept(self):
        target = os.path.join(self.d, "real-limits.conf")
        self.write(target, "cnc soft nofile 1\n")
        os.makedirs(checks.LIMITS_DIR)
        os.symlink(target, checks.LIMITS_CONF)
        ok, _ = checks.apply_fix(RecordingHost(), self.check(
            "ulimit", domain="cnc", limit_type="soft", limit_item="nofile", expected_value="2"))
        self.assertTrue(ok)
        self.assertTrue(os.path.islink(checks.LIMITS_CONF))
        self.assertEqual(open(target).read(), "cnc soft nofile 2\n")
        self.assertEqual(os.listdir(self.d).count("real-limits.conf.nfupgrader.tmp"), 0)

    def test_tab_separated_kernel_line_replaced(self):
        self.write(checks.SYSCTL_CONF, "kernel.shmmax\t=\t1\n -kernel.shmmax = 3\nvm.x=1\n")
        checks.apply_fix(RecordingHost(), self.check("kernel_param", param="kernel.shmmax",
                                                     expected_value="2"))
        self.assertEqual(open(checks.SYSCTL_CONF).read(), "vm.x=1\nkernel.shmmax=2\n")

    def test_prefix_param_does_not_clobber_longer_key(self):
        self.write(checks.SYSCTL_CONF, "kernel.shmmax = 5\n")
        checks.apply_fix(RecordingHost(), self.check("kernel_param", param="kernel.shm",
                                                     expected_value="1"))
        self.assertEqual(open(checks.SYSCTL_CONF).read(), "kernel.shmmax = 5\nkernel.shm=1\n")

    def test_unreadable_target_is_a_write_failure_and_leaves_no_temp(self):
        os.makedirs(checks.LIMITS_CONF)        # a directory: read fails with EISDIR, not ENOENT
        ok, detail = checks.apply_fix(RecordingHost(), self.check(
            "ulimit", domain="cnc", limit_type="soft", limit_item="nofile", expected_value="2"))
        self.assertFalse(ok)
        self.assertIn("could not write", detail)
        self.assertEqual(os.listdir(checks.LIMITS_DIR), ["99-netflex.conf"])

    def test_unencodable_value_is_a_write_failure(self):
        ok, detail = checks.apply_fix(RecordingHost(), self.check(
            "ulimit", domain="cnc", limit_type="soft", limit_item="nofile",
            expected_value="\ud800"))
        self.assertFalse(ok)
        self.assertIn("could not write", detail)
        ok, detail = checks.apply_fix(RecordingHost(), self.check(
            "kernel_param", param="a.b", expected_value="\ud800"))
        self.assertTrue(ok)
        self.assertIn("not persisted", detail)

    # --- honest ulimit result ---

    def ulimit_check(self, value="65536"):
        return self.check("ulimit", domain="cnc", limit_type="soft", limit_item="nofile",
                          expected_value=value)

    def test_ulimit_overridden_by_later_file_warns(self):
        later = os.path.join(checks.LIMITS_DIR, "cnc.conf")      # sorts after 99-netflex.conf
        self.write(later, "cnc soft nofile 1024\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.ulimit_check())
        self.assertTrue(ok)
        self.assertEqual(detail, "WARNING: written to {} but overridden by {} (1024)".format(
            checks.LIMITS_CONF, later))
        self.assertFalse(checks.KINDS["ulimit"](RecordingHost(), self.ulimit_check()["params"])[0])

    def test_ulimit_earlier_entries_do_not_warn(self):
        self.write(checks.LIMITS_MAIN, "cnc soft nofile 1024\n")
        self.write(os.path.join(checks.LIMITS_DIR, "10-a.conf"), "cnc soft nofile 2048\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.ulimit_check())
        self.assertTrue(ok)
        self.assertFalse(detail.startswith("WARNING"), detail)
        self.assertTrue(checks.KINDS["ulimit"](RecordingHost(), self.ulimit_check()["params"])[0])

    # --- single source for cmm nproc: netFLEX's linux-nproc.conf -> 99-nproc.conf ---

    def nproc(self, limit_type="soft"):
        staged = os.path.join(self.d, "cnc_stage", "features", "linux-nproc.conf")
        installed = os.path.join(self.d, "cnc", "features", "linux-nproc.conf")
        return self.check("ulimit", domain="cmm", limit_type=limit_type, limit_item="nproc",
                          compare="min", reference=[staged, installed],
                          target=os.path.join(checks.LIMITS_DIR, "99-nproc.conf")), staged, installed

    def test_nproc_expected_comes_from_reference_staged_first(self):
        c, staged, installed = self.nproc()
        self.write(installed, "cmm soft nproc 16384\ncmm hard nproc 16384\n")
        self.write(os.path.join(checks.LIMITS_DIR, "99-nproc.conf"), "cmm soft nproc 16384\n")
        ok, detail = checks.KINDS["ulimit"](RecordingHost(), c["params"])
        self.assertTrue(ok, detail)
        self.assertIn("at least 16384 from " + installed, detail)
        self.write(staged, "cmm soft nproc 125883\ncmm hard nproc 125883\n")
        ok, detail = checks.KINDS["ulimit"](RecordingHost(), c["params"])
        self.assertFalse(ok)
        self.assertIn("cmm nproc = 16384", detail)
        self.assertIn("at least 125883 from " + staged, detail)

    def test_nproc_greater_or_unlimited_is_ok(self):
        c, staged, _ = self.nproc()
        self.write(staged, "cmm soft nproc 125883\n")
        target = c["params"]["target"]
        for value, want in (("125883", True), ("200000", True), ("unlimited", True),
                            ("125882", False), ("lots", False)):
            self.write(target, "cmm soft nproc %s\n" % value)
            self.assertEqual(checks.KINDS["ulimit"](RecordingHost(), c["params"])[0], want, value)

    def test_nproc_without_reference_fails_and_has_no_fix(self):
        c, _, _ = self.nproc()
        ok, detail = checks.KINDS["ulimit"](RecordingHost(), c["params"])
        self.assertFalse(ok)
        self.assertIn("no reference value", detail)
        self.assertIsNone(checks.describe_fix(c))
        self.assertFalse(checks.apply_fix(RecordingHost(), c)[0])

    def test_nproc_fix_edits_99_nproc_and_drops_our_copy(self):
        c, staged, _ = self.nproc()
        self.write(staged, "cmm soft nproc 125883\ncmm hard nproc 125883\n")
        target = c["params"]["target"]
        self.write(target, "cmm soft nproc 16384\ncmm hard nproc 16384\n")
        # an earlier fix wrote a second copy into our own file (holmvm32)
        self.write(checks.LIMITS_CONF, "cnc soft nofile 65536\ncmm soft nproc 32768\n")
        self.assertEqual(checks.describe_fix(c),
                         "set 'cmm soft nproc 125883' in {} (removing any copy in {})".format(
                             target, checks.LIMITS_CONF))
        ok, detail = checks.apply_fix(RecordingHost(), c)
        self.assertTrue(ok, detail)
        self.assertFalse(detail.startswith("WARNING"), detail)
        self.assertEqual(open(target).read(), "cmm hard nproc 16384\ncmm soft nproc 125883\n")
        self.assertEqual(open(checks.LIMITS_CONF).read(), "cnc soft nofile 65536\n")
        self.assertTrue(checks.KINDS["ulimit"](RecordingHost(), c["params"])[0])

    def test_nproc_fix_warns_when_still_overridden(self):
        c, staged, _ = self.nproc()
        self.write(staged, "cmm soft nproc 125883\n")
        later = os.path.join(checks.LIMITS_DIR, "zz-admin.conf")
        self.write(later, "cmm soft nproc 4096\n")
        ok, detail = checks.apply_fix(RecordingHost(), c)
        self.assertTrue(ok)
        self.assertEqual(detail, "WARNING: written to {} but overridden by {} (4096)".format(
            c["params"]["target"], later))

    def test_shipped_nproc_checks_use_single_source(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "nfupgrader", "checks.default.json")
        nproc = [c for c in checks.load_checks(path) if c["name"] in ("nproc_soft", "nproc_hard")]
        self.assertEqual(len(nproc), 2)
        for c in nproc:
            p = c["params"]
            self.assertEqual(p["compare"], "min")
            self.assertEqual(p["target"], "/etc/security/limits.d/99-nproc.conf")
            self.assertEqual(p["reference"], ["/usr/cnc_stage/features/linux-nproc.conf",
                                              "/usr/cnc/features/linux-nproc.conf"])
            self.assertNotIn("expected_value", p)

    # --- honest kernel_param persistence ---

    def kp(self, value="68719476736"):
        return self.check("kernel_param", param="kernel.shmmax", expected_value=value)

    def test_sysctl_conf_wins_at_boot_warns(self):
        self.write(checks.SYSCTL_MAIN, "kernel.shmmax = 1\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.kp())
        self.assertTrue(ok)
        self.assertEqual(detail, "WARNING: set live and persisted in {}, but {} sets "
                                 "kernel.shmmax=1 at boot".format(checks.SYSCTL_CONF,
                                                                  checks.SYSCTL_MAIN))

    def test_later_sysctl_d_file_warns_slash_form_too(self):
        later = os.path.join(checks.SYSCTL_DIRS[2], "zz-vendor.conf")
        self.write(later, "kernel/shmmax\t= 7\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.kp())
        self.assertTrue(ok)
        self.assertIn("but {} sets kernel.shmmax=7 at boot".format(later), detail)

    def test_same_basename_in_lower_dir_is_masked(self):
        self.write(os.path.join(checks.SYSCTL_DIRS[2], "99-netflex.conf"), "kernel.shmmax = 1\n")
        self.write(os.path.join(checks.SYSCTL_DIRS[1], "10-early.conf"), "kernel.shmmax = 2\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.kp())
        self.assertTrue(ok)
        self.assertFalse(detail.startswith("WARNING"), detail)

    def test_sysctl_conf_symlink_read_once_at_its_sysctl_d_position(self):
        # 99-sysctl.conf -> ../sysctl.conf: sysctl.conf is read there, not again at the end,
        # so a later sysctl.d file setting the expected value still wins.
        self.write(checks.SYSCTL_MAIN, "kernel.shmmax = 1\n")
        os.makedirs(checks.SYSCTL_DIRS[0])
        os.symlink(checks.SYSCTL_MAIN, os.path.join(checks.SYSCTL_DIRS[0], "99-sysctl.conf"))
        self.write(os.path.join(checks.SYSCTL_DIRS[0], "99-zz.conf"),
                   "kernel.shmmax = 68719476736\n")
        ok, detail = checks.apply_fix(RecordingHost(), self.kp())
        self.assertTrue(ok)
        self.assertFalse(detail.startswith("WARNING"), detail)

    def test_touchfile_uses_scmd(self):
        h = RecordingHost()
        ok, _ = checks.apply_fix(h, self.check("touchfile", file="ems"))
        self.assertTrue(ok)
        self.assertEqual(h.calls, [(["/usr/cnc/bin/scmd", "touch", "/usr/cnc/features/ems"], 30)])

    def test_apply_unfixable_and_exception(self):
        ok, detail = checks.apply_fix(RecordingHost(), self.check("dbcheck_clean"))
        self.assertFalse(ok)

        class Boom(object):
            def run(self, argv, timeout=None):
                raise OSError("exec failed")
        ok, detail = checks.apply_fix(Boom(), self.check("rpm_installed", pkg="ksh"))
        self.assertFalse(ok)
        self.assertIn("exec failed", detail)

    def test_results_carry_fix_only_when_failed_and_fixable(self):
        h = FakeHost({("rpm", "-q", "ksh"): RunResult(0, "ksh-1\n", ""),
                      ("rpm", "-q", "lvm2"): RunResult(1, "not installed\n", "")})
        cl = [self.check("rpm_installed", pkg="ksh"), self.check("rpm_installed", pkg="lvm2"),
              self.check("dbcheck_clean")]
        cl[0]["name"], cl[1]["name"], cl[2]["name"] = "ksh", "lvm2", "db"
        res = dict((r["name"], r) for r in checks.run_checks(h, cl, "preflight", "stage"))
        self.assertIsNone(res["ksh"]["fix"])                       # passed
        self.assertEqual(res["lvm2"]["fix"], "dnf install -y lvm2")
        self.assertIsNone(res["db"]["fix"])                        # not fixable

    def test_find_check_and_evaluate(self):
        cl = [dict(self.check("rpm_installed", pkg="ksh"), name="ksh"),
              dict(self.check("rpm_installed", pkg="x"), name="x", phase="upgrade")]
        self.assertEqual(checks.find_check(cl, "ksh", "preflight", "stage")["name"], "ksh")
        self.assertIsNone(checks.find_check(cl, "x", "preflight", "stage"))   # wrong phase
        self.assertIsNone(checks.find_check(cl, "nope", "preflight", "stage"))
        r = checks.evaluate(FakeHost({("rpm", "-q", "ksh"): RunResult(0, "ksh\n", "")}),
                            cl[0], "preflight")
        self.assertTrue(r["passed"])
        self.assertIsNone(r["fix"])


if __name__ == "__main__":
    unittest.main()

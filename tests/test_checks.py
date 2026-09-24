import unittest
from nfupgrader import checks
from nfupgrader.host import FakeHost, RunResult


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

    def test_ulimit(self):
        out = "# comment\ncmm soft nproc 32768\ncmm hard nproc 32768\n"
        cmd = ("sh", "-c", "cat /etc/security/limits.conf /etc/security/limits.d/*.conf 2>/dev/null")
        h = FakeHost({cmd: RunResult(0, out, "")})
        self.assertTrue(checks.KINDS["ulimit"](h, {"domain": "cmm", "limit_type": "soft",
                                                   "limit_item": "nproc", "expected_value": "32768"})[0])
        self.assertFalse(checks.KINDS["ulimit"](h, {"domain": "cmm", "limit_type": "hard",
                                                    "limit_item": "nproc", "expected_value": "999"})[0])


import os
import tempfile


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


class RecordingHost(object):
    """Maps tuple(argv) -> RunResult (default rc 0) and records (argv, timeout)."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def run(self, argv, timeout=None):
        self.calls.append((list(argv), timeout))
        return self.responses.get(tuple(argv), RunResult(0, "", ""))


class TestFixes(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self._saved = (checks.SYSCTL_CONF, checks.LIMITS_CONF)
        checks.SYSCTL_CONF = os.path.join(d, "sysctl.d", "99-netflex.conf")
        checks.LIMITS_CONF = os.path.join(d, "limits.d", "99-netflex.conf")

    def tearDown(self):
        checks.SYSCTL_CONF, checks.LIMITS_CONF = self._saved

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

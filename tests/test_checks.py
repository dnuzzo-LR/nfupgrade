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

    def test_result_shape(self):
        r = checks.run_checks(self._host(), self._checks(), "preflight", "upgrade", machtype="FEP")[0]
        for k in ("name", "description", "severity", "passed", "detail", "blocking"):
            self.assertIn(k, r)


if __name__ == "__main__":
    unittest.main()

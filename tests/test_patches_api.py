import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from nfupgrader import api, patches
from nfupgrader.host import RunResult
from tests.test_api import make_ctx

ROW_OK = {"path": "/depot/netFLEX-CORE-PATCH-5.4.0.29-3.x86_64.rpm", "name": "p1",
          "pkg": "netFLEX-CORE-PATCH", "version": "5.4.0.29-3", "arch": "x86_64",
          "reboot_loads": [], "status": "installable", "reason": "", "needs_stop": False}
ROW_BAD = dict(ROW_OK, path="/depot/old.rpm", status="not_applicable", reason="too old")
ROW_STOP = dict(ROW_OK, path="/depot/stop.rpm", needs_stop=True, reason="stop netFLEX")


def fake_gather(*a, **k):
    return {"installed": {"current_load": "inc54.029"}, "host_block": None,
            "app_up": False, "patches": [dict(ROW_OK), dict(ROW_BAD), dict(ROW_STOP)]}


class FakePopen(object):
    def __init__(self, rc):
        self.rc = rc

    def poll(self):
        return self.rc


class TestPatchesApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ctx = make_ctx(self.tmp, running=False)
        self.ctx.host.responses[("pgrep", "-f", "nf_patch")] = RunResult(1, "", "")
        self.ctx.host.responses[("pgrep", "-f", "nf_install")] = RunResult(1, "", "")
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")
        p = mock.patch.object(patches, "gather", side_effect=fake_gather)
        p.start()
        self.addCleanup(p.stop)

    def call(self, method, path, obj=None):
        body = json.dumps(obj).encode("utf-8") if obj is not None else b""
        st, hdrs, out = api.dispatch(method, path, {}, body,
                                     {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        return st, json.loads(out.decode("utf-8"))

    def audit(self):
        p = os.path.join(self.tmp, "audit.jsonl")
        return open(p).read() if os.path.exists(p) else ""

    def test_get_lists_rows(self):
        st, b = self.call("GET", "/api/patches")
        self.assertEqual(st, 200)
        self.assertEqual(len(b["patches"]), 3)
        self.assertTrue(b["available"])
        self.assertIsNone(b["reason"])
        self.assertFalse(b["running"])
        self.assertEqual(b["last"], {"trace": None, "result": None})

    def test_get_install_mode_unavailable(self):
        with mock.patch.object(api, "host_mode", return_value="install"):
            st, b = self.call("GET", "/api/patches")
        self.assertFalse(b["available"])
        self.assertEqual(b["reason"], "netFLEX is not installed")

    def test_get_preview_unavailable(self):
        self.ctx.state["preview"] = "fresh"
        st, b = self.call("GET", "/api/patches")
        self.assertFalse(b["available"])
        self.assertEqual(b["reason"], "preview mode")

    def test_running_without_job_handle(self):
        self.ctx.host.responses[("pgrep", "-f", "nf_patch")] = RunResult(0, "77\n", "")
        self.ctx.host.responses[("cat", "/proc/77/cmdline")] = RunResult(
            0, "/usr/bin/ksh\0/opt/nfupgrader/nf_patch\0x.rpm\0", "")
        st, b = self.call("GET", "/api/patches")
        self.assertTrue(b["running"])

    def test_last_result_from_trace(self):
        t = os.path.join(self.tmp, "patch_inc_h1.123")
        open(t, "w").write("2026-11-05 10:00:00 INFO  start\n"
                           "2026-11-05 10:00:01 ERROR RESULT: refused: x\n")
        st, b = self.call("GET", "/api/patches")
        self.assertEqual(b["last"]["trace"], t)
        self.assertIn("RESULT: refused: x", b["last"]["result"])

    def test_post_happy(self):
        st, b = self.call("POST", "/api/patches/install", {"path": ROW_OK["path"]})
        self.assertEqual(st, 202)
        self.assertEqual(b, {"accepted": True, "path": ROW_OK["path"],
                             "version": ROW_OK["version"]})
        self.assertIn("patch_install_started", self.audit())

    def test_post_bad_requests(self):
        body = b"not json"
        st, h, out = api.dispatch("POST", "/api/patches/install", {}, body,
                                  {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        self.assertEqual(st, 400)
        self.assertEqual(self.call("POST", "/api/patches/install", {})[0], 400)
        self.assertEqual(self.call("POST", "/api/patches/install", {"path": 5})[0], 400)

    def test_post_refused_not_installable(self):
        st, b = self.call("POST", "/api/patches/install", {"path": ROW_BAD["path"]})
        self.assertEqual(st, 400)
        self.assertEqual(b["error"], "too old")
        self.assertIn("patch_install_refused", self.audit())

    def test_post_not_in_list(self):
        st, b = self.call("POST", "/api/patches/install", {"path": "/etc/passwd"})
        self.assertEqual(st, 400)
        self.assertEqual(b["error"], "not in the depot list")
        self.assertIn("patch_install_refused", self.audit())
        self.assertNotIn("patch_install_started", self.audit())

    def test_post_needs_stop_refused(self):
        st, b = self.call("POST", "/api/patches/install", {"path": ROW_STOP["path"]})
        self.assertEqual(st, 400)
        self.assertIn("patch_install_refused", self.audit())

    def test_post_while_upgrader_running(self):
        ctx = make_ctx(self.tmp, running=True)
        ctx.host.responses[("pgrep", "-f", "nf_patch")] = RunResult(1, "", "")
        ctx.host.responses[("pgrep", "-f", "nf_install")] = RunResult(1, "", "")
        self.ctx = ctx
        self.sid = ctx.auth.redeem("TOK", "127.0.0.1")
        self.assertEqual(self.call("POST", "/api/patches/install",
                                   {"path": ROW_OK["path"]})[0], 409)

    def test_post_preview_409(self):
        self.ctx.state["preview"] = "fresh"
        st, b = self.call("POST", "/api/patches/install", {"path": ROW_OK["path"]})
        self.assertEqual(st, 409)
        self.assertEqual(b["error"], "preview mode")

    def test_post_inside_launch_grace_409(self):
        self.ctx.state["launched_at"] = 1000.0
        with mock.patch.object(api, "_now", return_value=1000.0 + api.FIX_LAUNCH_GRACE - 1):
            st, b = self.call("POST", "/api/patches/install", {"path": ROW_OK["path"]})
        self.assertEqual(st, 409)
        self.assertEqual(b["error"], "an upgrade or install was just launched")
        with mock.patch.object(api, "_now", return_value=1000.0 + api.FIX_LAUNCH_GRACE + 1):
            st, b = self.call("POST", "/api/patches/install", {"path": ROW_OK["path"]})
        self.assertEqual(st, 202)

    def test_post_while_nf_upgrade_running_409(self):
        self.ctx.host.responses[("pgrep", "-f", "nf_upgrade")] = RunResult(0, "88\n", "")
        self.ctx.host.responses[("cat", "/proc/88/cmdline")] = RunResult(
            0, "/usr/bin/ksh\0/opt/nfupgrader/nf_upgrade\0-c\0x\0-s\0", "")
        st, b = self.call("POST", "/api/patches/install", {"path": ROW_OK["path"]})
        self.assertEqual(st, 409)
        self.assertNotIn("patch_install_started", self.audit())

    def test_post_install_mode_409(self):
        with mock.patch.object(api, "host_mode", return_value="install"):
            st, b = self.call("POST", "/api/patches/install", {"path": ROW_OK["path"]})
        self.assertEqual(st, 409)
        self.assertEqual(b["error"], "netFLEX is not installed")

    def test_completion_failed_audits_once(self):
        self.ctx.state["patch_job"] = {"popen": FakePopen(2), "path": ROW_OK["path"],
                                       "version": "5.4.0.29-3", "client": "1.2.3.4"}
        st, b = self.call("GET", "/api/patches")
        self.assertNotIn("patch_job", self.ctx.state)
        self.assertNotIn("popen", json.dumps(b))
        self.call("GET", "/api/patches")
        self.assertEqual(self.audit().count("patch_install_failed"), 1)
        self.assertIn('"rc": 2', self.audit())

    def test_completion_ok_and_still_running(self):
        self.ctx.state["patch_job"] = {"popen": FakePopen(None), "path": "p",
                                       "version": "v", "client": "c"}
        self.call("GET", "/api/patches")
        self.assertIn("patch_job", self.ctx.state)
        self.ctx.state["patch_job"]["popen"] = FakePopen(0)
        self.call("GET", "/api/patches")
        self.assertIn("patch_install_done", self.audit())
        self.assertNotIn("patch_job", self.ctx.state)

    def test_progress_ignores_nf_patch(self):
        self.ctx.host.responses[("pgrep", "-f", "nf_patch")] = RunResult(0, "77\n", "")
        self.ctx.host.responses[("cat", "/proc/77/cmdline")] = RunResult(
            0, "/usr/bin/ksh\0/opt/nfupgrader/nf_patch\0x.rpm\0", "")
        st, b = self.call("GET", "/api/progress")
        self.assertNotEqual(b["status"], "running")
        st, b = self.call("GET", "/api/log/live")
        self.assertFalse(b["running"])


if __name__ == "__main__":
    unittest.main()

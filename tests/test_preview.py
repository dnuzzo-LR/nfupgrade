import json
import os
import tempfile
import unittest

from nfupgrader import api, preview
from tests.test_api import make_ctx, write_install_cfg


class TestPreviewData(unittest.TestCase):
    def test_scenarios(self):
        self.assertEqual(preview.SCENARIOS, ("before", "running", "failed", "done"))
        self.assertEqual(preview.scenario_from_env("1"), "before")
        self.assertEqual(preview.scenario_from_env("failed"), "failed")
        self.assertEqual(preview.scenario_from_env("bogus"), "before")
        self.assertIsNone(preview.scenario_from_env(""))
        self.assertIsNone(preview.scenario_from_env(None))

    def test_status_shapes(self):
        before = preview.status("before", "h1")
        self.assertEqual((before["mode"], before["installed"], before["unfinished"], before["state"]),
                         ("install", False, False, None))
        failed = preview.status("failed", "h1")
        self.assertTrue(failed["unfinished"])
        self.assertFalse(failed["running"])
        self.assertEqual(failed["state"], "ispostinstall")
        self.assertTrue(failed["fatal"])
        self.assertEqual(failed["info"]["LOADDIR_CREATED"], "yes")
        running = preview.status("running", "h1")
        self.assertTrue(running["running"] and running["unfinished"])
        done = preview.status("done", "h1")
        self.assertEqual((done["mode"], done["state"], done["installed"]), ("install", "isdone", True))
        for s in preview.SCENARIOS:
            self.assertEqual(preview.status(s, "h1")["preview"], s)


class TestPreviewApi(unittest.TestCase):
    """An installed box (make_ctx) with the preview switch on."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = make_ctx(self.tmp)._replace(user_config=write_install_cfg(self.tmp))
        self.ctx.state["preview"] = "before"
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")

    def call(self, method, path, payload=None, **q):
        body = json.dumps(payload).encode() if payload is not None else b""
        status, _, out = api.dispatch(method, path, dict((k, [v]) for k, v in q.items()), body,
                                      {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        return status, json.loads(out.decode())

    def test_installed_box_reads_as_install_mode(self):
        st, s = self.call("GET", "/api/install/status")
        self.assertEqual((st, s["mode"], s["preview"]), (200, "install", "before"))
        st, f = self.call("GET", "/api/config/fields")
        self.assertEqual(f["mode"], "install")

    def test_progress_follows_scenario(self):
        st, p = self.call("GET", "/api/progress")
        self.assertEqual((p["phase"], p["status"]), ("install", "not_started"))
        self.ctx.state["preview"] = "failed"
        st, p = self.call("GET", "/api/progress")
        self.assertEqual((p["current"], p["status"]), ("ispostinstall", "died"))
        self.ctx.state["preview"] = "done"
        st, p = self.call("GET", "/api/progress")
        self.assertEqual(p["status"], "done")

    def test_log_is_canned(self):
        self.ctx.state["preview"] = "failed"
        st, d = self.call("GET", "/api/log")
        self.assertEqual(st, 200)
        self.assertIn("FATAL", [e["severity"] for e in d["entries"]])
        st, d = self.call("GET", "/api/log", min="FATAL")
        self.assertTrue(all(e["severity"] == "FATAL" for e in d["entries"]))

    def test_launch_reset_fix_refused(self):
        for scenario in preview.SCENARIOS:
            self.ctx.state["preview"] = scenario
            st, d = self.call("POST", "/api/launch", {"mode": "install", "overrides": [], "reason": "x"})
            self.assertEqual(st, 409, scenario)
            self.assertIn("preview", d["error"])
            st, d = self.call("POST", "/api/install/reset", {"reason": "x"})
            self.assertEqual(st, 409, scenario)
            st, d = self.call("POST", "/api/checks/fix", {"name": "ksh", "phase": "install"})
            self.assertEqual(st, 409, scenario)
        audit = os.path.join(self.tmp, "audit.jsonl")
        text = open(audit).read() if os.path.exists(audit) else ""
        self.assertEqual([l for l in text.splitlines()
                          if '"launch"' in l or '"install_reset"' in l], [])

    def test_save_writes_preview_files_only(self):
        values = {"INCLOGDIR": self.tmp, "NEW_GENERIC": "5.3.0", "NEW_LOAD": "02",
                  "DEPOTFILE": "/d/x.rpm", "BOXTYPE": "SINGLE", "INSTALLWEBGUI": "YES"}
        before_cfg = self.ctx.user_config
        before_text = open(before_cfg).read()
        st, d = self.call("POST", "/api/config/save", {"values": values, "mode": "install"})
        self.assertEqual(st, 200)
        self.assertEqual(os.path.basename(d["saved"]), "nfupgrader_preview.cfg")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "nfupgrader_preview_cnc.cnfg")))
        self.assertEqual(open(before_cfg).read(), before_text)   # real saved config untouched
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nfupgrader_cnc.cnfg")))
        self.assertNotIn("user_config", self.ctx.state)
        self.assertEqual(self.ctx.user_config, before_cfg)

    def test_switch_scenario(self):
        st, d = self.call("POST", "/api/preview", {"scenario": "done"})
        self.assertEqual((st, self.ctx.state["preview"]), (200, "done"))
        st, d = self.call("POST", "/api/preview", {"scenario": "nope"})
        self.assertEqual(st, 400)

    def test_switch_refused_when_preview_off(self):
        self.ctx.state.pop("preview")
        st, d = self.call("POST", "/api/preview", {"scenario": "done"})
        self.assertEqual(st, 404)
        self.assertNotIn("preview", self.ctx.state)
        st, s = self.call("GET", "/api/install/status")
        self.assertEqual(s["mode"], "upgrade")
        self.assertNotIn("preview", s)


if __name__ == "__main__":
    unittest.main()

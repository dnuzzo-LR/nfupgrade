import json
import os
import tempfile
import unittest
from unittest import mock

from nfupgrader import api, license as lic
from nfupgrader.host import RunResult
from tests.test_api import make_ctx

PW = "s3cretPW"
LICENSE_DICT = {"NumNE": 5, "NumUsers": 2}


def _read_ok(host, path=None):
    return {"state": "ok", "license": dict(LICENSE_DICT), "error": ""}


class FakeSession(object):
    def __init__(self, running):
        self._r = running

    def running(self):
        return self._r


class TestLicenseApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = make_ctx(self.tmp, running=False)
        self.lic_path = os.path.join(self.tmp, "lic.ucs")
        self.ctx = self.ctx._replace(license_path=self.lic_path)
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")

    def call(self, method, path, obj=None):
        body = json.dumps(obj).encode("utf-8") if obj is not None else b""
        st, hdrs, out = api.dispatch(method, path, {}, body,
                                     {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        return st, json.loads(out.decode("utf-8"))

    def audit(self):
        p = os.path.join(self.tmp, "audit.jsonl")
        return open(p).read() if os.path.exists(p) else ""

    def req(self, **kw):
        d = {"password": PW, "values": {"NumNE": 9}, "expiry": "", "ne_on": None}
        d.update(kw)
        return d

    def test_get_license_states(self):
        with mock.patch.object(lic, "read_license", return_value={
                "state": "missing", "license": None, "error": ""}):
            st, b = self.call("GET", "/api/license")
        self.assertEqual(st, 200)
        self.assertEqual(b["state"], "missing")
        self.assertTrue(b["editable"])
        self.assertEqual(b["fields"], lic.FIELDS)
        with mock.patch.object(lic, "read_license", _read_ok):
            st, b = self.call("GET", "/api/license")
        self.assertEqual(b["state"], "ok")
        self.assertIn("expiry", b)
        self.ctx.host.responses[("test", "-f", lic.APP_UP)] = RunResult(0, "", "")
        with mock.patch.object(lic, "read_license", _read_ok):
            st, b = self.call("GET", "/api/license")
        self.assertFalse(b["editable"])
        self.assertIn("netFLEX is running", b["reason"])

    def test_requires_session(self):
        self.sid = "bad"
        self.assertEqual(self.call("GET", "/api/license")[0], 401)

    def test_unlock_wrong_password_403(self):
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "unlock", return_value={
                    "ok": False, "ne_types": [], "error": lic.WRONG_PW}):
            st, b = self.call("POST", "/api/license/unlock", {"password": PW})
        self.assertEqual(st, 403)
        self.assertEqual(b["error"], lic.WRONG_PW)
        self.assertNotIn(PW, json.dumps(b))

    def test_unlock_ok_and_state_409(self):
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "unlock", return_value={
                    "ok": True, "ne_types": [{"type": 1}], "error": ""}):
            st, b = self.call("POST", "/api/license/unlock", {"password": PW})
        self.assertEqual(st, 200)
        self.assertTrue(b["ok"])
        with mock.patch.object(lic, "read_license", return_value={
                "state": "missing", "license": None, "error": ""}):
            self.assertEqual(
                self.call("POST", "/api/license/unlock", {"password": PW})[0], 409)

    def test_review_validation_400_and_ok(self):
        unlocked = {"ok": True, "ne_types": [{"type": 3, "name": "x", "group": 0}],
                    "error": ""}
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "unlock", return_value=unlocked):
            st, b = self.call("POST", "/api/license/review",
                              self.req(values={"NumNE": "abc"}))
            self.assertEqual(st, 400)
            self.assertTrue(b["errors"])
            st, b = self.call("POST", "/api/license/review", self.req())
        self.assertEqual(st, 200)
        self.assertEqual([c["key"] for c in b["changes"]], ["NumNE"])
        self.assertNotIn(PW, json.dumps(b))
        bad = {"ok": False, "ne_types": [], "error": lic.WRONG_PW}
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "unlock", return_value=bad):
            self.assertEqual(
                self.call("POST", "/api/license/review", self.req())[0], 403)

    def _busy(self, how):
        if how == "app":
            self.ctx.host.responses[("test", "-f", lic.APP_UP)] = RunResult(0, "", "")
        elif how == "running":
            self.ctx.host.responses.update(
                {("pgrep", "-f", "nf_upgrader"): RunResult(0, "4242\n", ""),
                 ("cat", "/proc/4242/cmdline"): RunResult(
                     0, "/usr/bin/ksh\0/opt/nfupgrader/nf_upgrader\0-s\0", "")})
        elif how == "bootmenu":
            self.ctx.state["bootmenu"] = FakeSession(True)
        elif how == "snapshot":
            self.ctx = self.ctx._replace(
                runner=mock.Mock(busy=mock.Mock(return_value=True)))
        elif how == "preview":
            self.ctx.state["preview"] = "fresh"

    def test_save_refused_when_busy(self):
        for how in ("app", "running", "bootmenu", "snapshot", "preview", "fix"):
            self.setUp()
            with mock.patch.object(lic, "read_license", _read_ok), \
                    mock.patch.object(lic, "apply") as ap, \
                    mock.patch.object(lic, "backup") as bk:
                if how == "fix":
                    self.assertTrue(api._FIX_LOCK.acquire(False))
                    try:
                        st, b = self.call("POST", "/api/license/save", self.req())
                    finally:
                        api._FIX_LOCK.release()
                else:
                    self._busy(how)
                    st, b = self.call("POST", "/api/license/save", self.req())
            self.assertEqual(st, 409, how)
            self.assertFalse(ap.called, how)
            self.assertFalse(bk.called, how)
            self.assertNotIn("license_saved", self.audit(), how)

    def test_save_happy_audits_without_password(self):
        order = []
        res = {"ok": True, "applied": ["NumNE"], "not_applied": [], "error": "",
               "license": {"NumNE": 9}}
        seen = {}

        def fake_apply(host, pw, d, path):
            order.append("apply")
            seen["d"] = d
            return res

        def fake_backup(path):
            order.append("backup")
            return "/b/lic.bak"

        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "backup", fake_backup), \
                mock.patch.object(lic, "apply", fake_apply):
            st, b = self.call("POST", "/api/license/save", self.req(
                set_lines=["set NumNE 1"]))
        self.assertEqual(st, 200)
        self.assertEqual(order, ["backup", "apply"])
        self.assertEqual(b["backup"], "/b/lic.bak")
        self.assertEqual(seen["d"]["set_lines"], ["set numne 9"])
        a = self.audit()
        self.assertIn("license_saved", a)
        self.assertIn("NumNE", a)
        self.assertNotIn(PW, a)
        self.assertNotIn(PW, json.dumps(b))
        self.assertFalse(api._FIX_LOCK.locked())

    def test_save_validation_and_no_changes(self):
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "apply") as ap, \
                mock.patch.object(lic, "backup") as bk:
            st, b = self.call("POST", "/api/license/save",
                              self.req(values={"NumNE": "x"}))
            self.assertEqual(st, 400)
            st, b = self.call("POST", "/api/license/save",
                              self.req(values={"NumNE": 5}))
            self.assertEqual((st, b), (200, {"ok": True, "applied": []}))
        self.assertFalse(ap.called or bk.called)

    def test_save_backup_failure_does_not_apply(self):
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "backup", side_effect=OSError("disk full")), \
                mock.patch.object(lic, "apply") as ap:
            st, b = self.call("POST", "/api/license/save", self.req())
        self.assertEqual(st, 500)
        self.assertFalse(ap.called)
        self.assertFalse(b["ok"])
        self.assertFalse(api._FIX_LOCK.locked())

    def test_save_failure_audited(self):
        res = {"ok": False, "applied": [], "not_applied": ["NumNE"],
               "error": "boom", "license": None}
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "backup", return_value="/b"), \
                mock.patch.object(lic, "apply", return_value=res):
            st, b = self.call("POST", "/api/license/save", self.req())
        self.assertEqual(st, 200)
        self.assertFalse(b["ok"])
        a = self.audit()
        self.assertIn("license_save_failed", a)
        self.assertNotIn("license_saved", a)
        self.assertNotIn(PW, a)

    def test_create_only_when_missing(self):
        missing = {"state": "missing", "license": None, "error": ""}
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "create_from_template") as cr:
            self.assertEqual(self.call("POST", "/api/license/create")[0], 409)
        self.assertFalse(cr.called)
        reads = [missing, {"state": "old_format", "license": None, "error": "e"},
                 _read_ok(None)]
        with mock.patch.object(lic, "read_license", side_effect=reads), \
                mock.patch.object(lic, "create_from_template",
                                  return_value="/t/def") as cr, \
                mock.patch.object(lic, "migrate",
                                  return_value={"ok": True, "rc": 0, "error": ""}) as mg:
            st, b = self.call("POST", "/api/license/create")
        self.assertEqual(st, 200)
        self.assertTrue(mg.called)
        self.assertEqual(b["state"], "ok")
        self.assertIn("license_created", self.audit())
        self.ctx.host.responses[("test", "-f", lic.APP_UP)] = RunResult(0, "", "")
        with mock.patch.object(lic, "read_license", return_value=missing), \
                mock.patch.object(lic, "create_from_template") as cr2:
            self.assertEqual(self.call("POST", "/api/license/create")[0], 409)
        self.assertFalse(cr2.called)

    def test_migrate_only_when_old(self):
        with mock.patch.object(lic, "read_license", _read_ok), \
                mock.patch.object(lic, "migrate") as mg:
            self.assertEqual(self.call("POST", "/api/license/migrate")[0], 409)
        self.assertFalse(mg.called)
        old = {"state": "old_format", "license": None, "error": "e"}
        with mock.patch.object(lic, "read_license", return_value=old), \
                mock.patch.object(lic, "migrate",
                                  return_value={"ok": True, "rc": 6, "error": ""}):
            st, b = self.call("POST", "/api/license/migrate")
        self.assertEqual((st, b), (200, {"ok": True, "rc": 6}))
        self.assertIn("license_migrated", self.audit())

    def test_preview_refuses_writes(self):
        self.ctx.state["preview"] = "fresh"
        for p in ("save", "create", "migrate"):
            self.assertEqual(self.call("POST", "/api/license/" + p, self.req())[0], 409)


if __name__ == "__main__":
    unittest.main()

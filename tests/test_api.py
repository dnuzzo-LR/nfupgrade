import json
import os
import tempfile
import unittest
from nfupgrader import api
from nfupgrader.auth import TokenAuth
from nfupgrader.audit import AuditLog
from nfupgrader.host import FakeHost, RunResult


def make_ctx(tmp, running=True):
    host = FakeHost({
        ("pgrep", "-f", "nf_upgrader"): RunResult(0 if running else 1, "", ""),
        # localinfo commands (app down: no tier-2 needed)
        ("rpm", "-qi", "netFLEX-CORE"): RunResult(0, "Version     : 5.4.0\nRelease     : 29\n", ""),
        ("rpm", "-qi", "netFLEX-CORE-PATCH"): RunResult(1, "", ""),
        ("rpm", "-qi", "netFLEX-CORE-IPATCH"): RunResult(1, "", ""),
        ("readlink", "/usr/cnc"): RunResult(0, "/usr/inc/inc54.029\n", ""),
        ("readlink", "/usr/cnc_stage"): RunResult(1, "", ""),
        ("readlink", "/usr/cnc_saved"): RunResult(1, "", ""),
        ("test", "-f", "/usr/cnc/.CNC_UP"): RunResult(1, "", ""),
        ("cat", "/usr/cnc/.GR_STATUS"): RunResult(1, "", ""),
        ("test", "-f", "/usr/cnc/grestore.pid"): RunResult(1, "", ""),
        ("df", "-kP"): RunResult(0, "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                                    "/dev/sda4 100 40 60 40% /usr4\n", ""),
    })
    open(os.path.join(tmp, ".h1_STATE"), "w").write("swscopycnc\n")
    open(os.path.join(tmp, "trace"), "w").write("2026-09-18 10:00:00 INFO  hi\n")
    cnfg = os.path.join(tmp, "cnc.cnfg")
    open(cnfg, "w").write("h1 1 h1 1 1 0\n")
    return api.Context(
        host=host, hostname="h1", inclogdir=tmp, phase="stage",
        tracefile=os.path.join(tmp, "trace"),
        auth=TokenAuth(token="TOK"), audit=AuditLog(os.path.join(tmp, "audit.jsonl")),
        webdir=tmp, nf_upgrade_path="/x/nf_upgrade", cnc_cnfg_path=cnfg,
    )


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = make_ctx(self.tmp)

    def _session(self):
        return self.ctx.auth.redeem("TOK", "127.0.0.1")

    def test_progress_requires_session(self):
        status, _, _ = api.dispatch("GET", "/api/progress", {}, b"", {}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 401)

    def test_progress_with_session_returns_record(self):
        sid = self._session()
        status, _, body = api.dispatch(
            "GET", "/api/progress", {}, b"", {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertEqual(data["current"], "swscopycnc")
        self.assertEqual(data["completed"], 3)
        self.assertEqual(data["status"], "running")

    def test_log_endpoint_filters_and_paginates(self):
        sid = self._session()
        status, _, body = api.dispatch(
            "GET", "/api/log", {"offset": ["0"]}, b"", {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertIn("entries", data)
        self.assertIn("next_offset", data)

    def test_redeem_sets_cookie_and_redirects(self):
        status, headers, _ = api.dispatch(
            "GET", "/auth", {"token": ["TOK"]}, b"", {}, "127.0.0.1", self.ctx)
        self.assertIn(status, (302, 303))
        self.assertTrue(any(h[0].lower() == "set-cookie" for h in headers))

    def test_redeem_bad_token_401(self):
        status, _, _ = api.dispatch(
            "GET", "/auth", {"token": ["NOPE"]}, b"", {}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 401)

    def test_launch_needs_session(self):
        status, _, _ = api.dispatch(
            "POST", "/api/launch", {}, json.dumps({"mode": "stage"}).encode(),
            {}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 401)

    def test_launch_records_audit_and_accepts(self):
        sid = self._session()
        status, _, body = api.dispatch(
            "POST", "/api/launch", {}, json.dumps({"mode": "stage"}).encode(),
            {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 202)
        events = [json.loads(l)["event"] for l in
                  open(os.path.join(self.tmp, "audit.jsonl")).read().splitlines()]
        self.assertIn("launch", events)

    def test_info_requires_session(self):
        status, _, _ = api.dispatch("GET", "/api/info", {}, b"", {}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 401)

    def test_info_with_session_returns_facts(self):
        sid = self._session()
        status, _, body = api.dispatch(
            "GET", "/api/info", {}, b"", {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertIn("topology", data)
        self.assertIn("versions", data)
        self.assertEqual(data["versions"]["core"], "5.4.0-29")

    def test_site_requires_session(self):
        status, _, _ = api.dispatch("GET", "/api/site", {}, b"", {}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 401)

    def test_site_with_session_lists_hosts(self):
        open(self.ctx.cnc_cnfg_path, "w").write("h1 1 h2 2 1 0\nh2 2 h1 1 1 0\n")
        peer = FakeHost({("true",): RunResult(255, "", "")})   # h2 unreachable
        ctx = self.ctx._replace(peer_host_factory=lambda n: peer)
        sid = ctx.auth.redeem("TOK", "127.0.0.1")
        status, _, body = api.dispatch(
            "GET", "/api/site", {}, b"", {"nfu_sid": sid}, "127.0.0.1", ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        names = [h["name"] for h in data["hosts"]]
        self.assertEqual(sorted(names), ["h1", "h2"])

    def test_launch_bad_mode_400(self):
        sid = self._session()
        status, _, _ = api.dispatch(
            "POST", "/api/launch", {}, json.dumps({"mode": "x"}).encode(),
            {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()

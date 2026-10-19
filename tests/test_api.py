import collections
import datetime
import json
import os
import tempfile
import unittest
from nfupgrader import api, checks, cnccnfg
from nfupgrader import reports, snapjobs
from nfupgrader.auth import TokenAuth
from nfupgrader.audit import AuditLog
from nfupgrader.host import FakeHost, RunResult


def running_responses(running):
    """FakeHost entries for launcher.upgrader_running: a real-looking nf_upgrader
    process (ksh running the script) when running, else no pgrep match."""
    if not running:
        return {("pgrep", "-f", "nf_upgrader"): RunResult(1, "", "")}
    return {("pgrep", "-f", "nf_upgrader"): RunResult(0, "4242\n", ""),
            ("cat", "/proc/4242/cmdline"):
                RunResult(0, "/usr/bin/ksh\0/opt/nfupgrader/nf_upgrader\0-s\0", "")}


def make_ctx(tmp, running=True):
    responses = running_responses(running)
    responses.update({
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
                                    "/dev/sda4 100000000 10000000 90000000 10% /usr4\n", ""),
        # netflex_installed / host_mode: model an already-installed box, so
        # existing stage/upgrade tests aren't read as a fresh install.
        ("rpm", "-q", "netFLEX-CORE"): RunResult(0, "netFLEX-CORE-5.4.0-29\n", ""),
        # default-check commands: stage pre-flight all pass; upgrade blocks on
        # staged-present (no staged load) — see readlink /usr/cnc_stage rc 1 above.
        ("dbcheck", "-AV"): RunResult(0, "clean\n", ""),
        ("find", "/depot", "-maxdepth", "1", "-name", "netFLEX-CORE*.rpm"):
            RunResult(0, "/depot/netFLEX-CORE-5.5.0.03-1.x86_64.rpm\n", ""),
    })
    host = FakeHost(responses)
    open(os.path.join(tmp, ".h1_STATE"), "w").write("swscopycnc\n")
    open(os.path.join(tmp, "trace"), "w").write("2026-09-18 10:00:00 INFO  hi\n")
    cnfg = os.path.join(tmp, "cnc.cnfg")
    open(cnfg, "w").write("h1 1 h1 1 1 0\n")
    # Use a controlled fixture, not the shipped default, so these tests stay
    # stable as the production check list grows.
    checks_path = os.path.join(os.path.dirname(__file__), "fixtures", "checks.test.json")
    return api.Context(
        host=host, hostname="h1", inclogdir=tmp, phase="stage",
        tracefile=os.path.join(tmp, "trace"),
        auth=TokenAuth(token="TOK"), audit=AuditLog(os.path.join(tmp, "audit.jsonl")),
        webdir=tmp, nf_upgrade_path="/x/nf_upgrade", cnc_cnfg_path=cnfg,
        checks_path=checks_path, state={}, depot_dirs=["/depot"],
    )


class TestApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ctx = make_ctx(self.tmp)

    def _session(self):
        # Cached: the token is single-use, but several tests (e.g. those
        # driving /api/launch through the _launch() helper) call _session()
        # more than once per test and need the same session back.
        if getattr(self, "_sid", None) is None:
            self._sid = self.ctx.auth.redeem("TOK", "127.0.0.1")
        return self._sid

    def _set_running(self, running):
        self.ctx.host.responses.update(running_responses(running))

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
        self._set_running(False)
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

    def test_checks_endpoint(self):
        sid = self._session()
        status, _, body = api.dispatch(
            "GET", "/api/checks", {"when": ["preflight"], "phase": ["stage"]},
            b"", {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertIn("results", data)
        self.assertIn("blocking", data)

    def test_launch_blocked_by_preflight_error(self):
        self._set_running(False)
        sid = self._session()
        status, _, body = api.dispatch(
            "POST", "/api/launch", {}, json.dumps({"mode": "upgrade"}).encode(),
            {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 409)
        data = json.loads(body.decode())
        self.assertTrue(len(data["blocking"]) >= 1)

    def _launch(self, payload):
        # make_ctx defaults to nf_upgrader running (the progress tests rely on
        # it); launches are refused while it runs, so exercise them idle.
        self._set_running(False)
        sid = self._session()
        status, _, body = api.dispatch(
            "POST", "/api/launch", {}, json.dumps(payload).encode(),
            {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        return status, json.loads(body.decode())

    def _audit(self):
        path = os.path.join(self.tmp, "audit.jsonl")
        if not os.path.exists(path):
            return []
        return [json.loads(l) for l in open(path).read().splitlines()]

    def test_launch_partial_override_names_missing(self):
        self.ctx.host.responses[("dbcheck", "-AV")] = RunResult(0, "ERROR: x\n", "")
        status, data = self._launch({"mode": "upgrade", "overrides": ["staged-present"],
                                     "reason": "known good"})
        self.assertEqual(status, 409)
        self.assertEqual(data["not_overridden"], ["dbcheck-clean"])
        self.assertEqual(sorted(b["name"] for b in data["blocking"]),
                         ["dbcheck-clean", "staged-present"])

    def test_launch_full_override_requires_reason(self):
        status, _ = self._launch({"mode": "upgrade", "overrides": ["staged-present"]})
        self.assertEqual(status, 400)
        status, _ = self._launch({"mode": "upgrade", "overrides": ["staged-present"],
                                  "reason": "   "})
        self.assertEqual(status, 400)
        status, _ = self._launch({"mode": "upgrade", "overrides": ["staged-present"],
                                  "reason": 42})
        self.assertEqual(status, 400)

    def test_launch_full_override_with_reason_audits_names(self):
        self.ctx.host.responses[("dbcheck", "-AV")] = RunResult(0, "ERROR: x\n", "")
        status, _ = self._launch({"mode": "upgrade",
                                  "overrides": ["staged-present", "dbcheck-clean"],
                                  "reason": "known good"})
        self.assertEqual(status, 202)
        recs = self._audit()
        ovr = [r for r in recs if r["event"] == "override"]
        self.assertEqual(len(ovr), 1)
        self.assertEqual(sorted(ovr[0]["checks"]), ["dbcheck-clean", "staged-present"])
        self.assertEqual(ovr[0]["reason"], "known good")
        self.assertIn("launch", [r["event"] for r in recs])

    def test_launch_stale_override_name_ignored(self):
        status, _ = self._launch({"mode": "upgrade",
                                  "overrides": ["staged-present", "disk-usr4", "no-such"],
                                  "reason": "known good"})
        self.assertEqual(status, 202)
        ovr = [r for r in self._audit() if r["event"] == "override"]
        self.assertEqual(ovr[0]["checks"], ["staged-present"])

    def test_launch_no_blocking_no_override_record(self):
        status, _ = self._launch({"mode": "stage"})
        self.assertEqual(status, 202)
        events = [r["event"] for r in self._audit()]
        self.assertNotIn("override", events)
        self.assertIn("launch", events)

    def test_launch_overrides_must_be_list(self):
        status, _ = self._launch({"mode": "upgrade", "overrides": "staged-present",
                                  "reason": "x"})
        self.assertEqual(status, 400)

    def test_launch_legacy_override_flag_no_longer_bypasses(self):
        status, data = self._launch({"mode": "upgrade", "override": True, "reason": "x"})
        self.assertEqual(status, 409)
        self.assertEqual(data["not_overridden"], ["staged-present"])

    def test_config_fields(self):
        sid = self._session()
        status, _, body = api.dispatch("GET", "/api/config/fields", {}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        fields = json.loads(body.decode())["fields"]
        self.assertTrue(any(f["name"] == "NEW_GENERIC" for f in fields))

    def test_config_inclogdir_defaults_to_nfupgrader_logdir(self):
        sid = self._session()
        status, _, body = api.dispatch("GET", "/api/config/fields", {}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        inc = [f for f in json.loads(body.decode())["fields"] if f["name"] == "INCLOGDIR"][0]
        self.assertEqual(inc["default"], self.ctx.inclogdir)
        # the shared field table itself is not modified
        from nfupgrader import cfg
        self.assertNotEqual([f for f in cfg.FIELDS if f["name"] == "INCLOGDIR"][0]["default"],
                            self.ctx.inclogdir)

    def test_config_depots(self):
        sid = self._session()
        status, _, body = api.dispatch("GET", "/api/config/depots", {}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertEqual(data["dirs"], ["/depot"])
        self.assertEqual([d["name"] for d in data["depots"]],
                         ["netFLEX-CORE-5.5.0.03-1.x86_64.rpm"])

    def test_config_preview_reports_issues_and_rendered(self):
        sid = self._session()
        payload = json.dumps({"values": {"NEW_GENERIC": "5.5.0"}}).encode()  # missing required
        status, _, body = api.dispatch("POST", "/api/config/preview", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertTrue(any(i["level"] == "ERROR" for i in data["issues"]))
        self.assertIn("PROMPTFORVALIDATE=NO", data["rendered"])

    def test_config_import_shows_values_and_override(self):
        sid = self._session()
        p = os.path.join(self.tmp, "user.cfg")
        open(p, "w").write('INCLOGDIR="/usr/cnc/logs"\nPROMPTFORVALIDATE=YES\n')
        status, _, body = api.dispatch("GET", "/api/config/import", {"path": [p]}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertEqual(data["values"]["INCLOGDIR"], "/usr/cnc/logs")
        self.assertTrue(data["forces_promptforvalidate"])   # original had YES; we force NO

    def test_config_save_sets_active_and_writes_file(self):
        sid = self._session()
        payload = json.dumps({"values": {
            "INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0", "NEW_LOAD": "03",
            "DEPOTFILE": "/depot/netFLEX-CORE-5.5.0.03-1.x86_64.rpm"}}).encode()
        status, _, body = api.dispatch("POST", "/api/config/save", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        saved = json.loads(body.decode())["saved"]
        self.assertIn("PROMPTFORVALIDATE=NO", open(saved).read())
        self.assertEqual(self.ctx.state["user_config"], saved)

    def test_config_save_rejects_invalid(self):
        sid = self._session()
        payload = json.dumps({"values": {"NEW_GENERIC": "5.5.0"}}).encode()  # missing required
        status, _, body = api.dispatch("POST", "/api/config/save", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 400)
        self.assertTrue(len(json.loads(body.decode())["issues"]) >= 1)

    def test_config_preview_rejects_non_object_body(self):
        sid = self._session()
        for raw in (b"[1]", b'"x"', b"7", b"null", b"{", b"\xff"):
            status, _, body = api.dispatch("POST", "/api/config/preview", {}, raw,
                                           {"nfu_sid": sid}, "127.0.0.1", self.ctx)
            self.assertEqual((status, json.loads(body.decode())),
                             (400, {"error": "bad json"}), raw)

    def test_config_save_rejects_non_object_body(self):
        sid = self._session()
        for raw in (b"[1]", b'"x"', b"7", b"null", b"{", b"\xff"):
            status, _, body = api.dispatch("POST", "/api/config/save", {}, raw,
                                           {"nfu_sid": sid}, "127.0.0.1", self.ctx)
            self.assertEqual((status, json.loads(body.decode())),
                             (400, {"error": "bad json"}), raw)

    def test_fields_install(self):
        sid = self._session()
        status, _, body = api.dispatch("GET", "/api/config/fields", {"mode": ["install"]}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        names = [f["name"] for f in data["fields"]]
        self.assertIn("BOXTYPE", names)
        self.assertNotIn("UNLINKDIR", names)
        self.assertEqual(data["mode"], "install")
        self.assertEqual(data["hostname"], "h1")

    def test_fields_bad_mode_400(self):
        sid = self._session()
        status, _, body = api.dispatch("GET", "/api/config/fields", {"mode": ["bogus"]}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 400)

    def test_hosts_endpoint(self):
        sid = self._session()
        hosts_file = os.path.join(self.tmp, "hosts")
        open(hosts_file, "w").write("10.0.0.2 h2\n10.0.0.3 h3 alias3\n")
        ctx = self.ctx._replace(hosts_path=hosts_file)
        status, _, body = api.dispatch("GET", "/api/install/hosts", {}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        names = [h["name"] for h in data["hosts"]]
        self.assertEqual(names, ["h2", "h3", "alias3"])

    def test_preview_install_renders_cnc_and_warns_unknown_mate(self):
        sid = self._session()
        hosts_file = os.path.join(self.tmp, "hosts")
        open(hosts_file, "w").write("10.0.0.2 h2\n")
        ctx = self.ctx._replace(hosts_path=hosts_file)
        values = {"BOXTYPE": "FEP", "MATEHOST": "zz", "MASTERHOST": "h1",
                  "INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0", "NEW_LOAD": "03",
                  "DEPOTFILE": "/depot/x.rpm"}
        payload = json.dumps({"values": values, "mode": "install"}).encode()
        status, _, body = api.dispatch("POST", "/api/config/preview", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        warn = [i for i in data["issues"] if i["field"] == "MATEHOST" and i["level"] == "WARNING"]
        self.assertEqual(len(warn), 1)
        self.assertIn("zz is not in /etc/hosts", warn[0]["message"])
        self.assertIn("h1 1 zz 1 1 9", data["cnc_cnfg"])

    def test_preview_install_invalid_has_no_cnc(self):
        sid = self._session()
        values = {"BOXTYPE": "FEP"}
        payload = json.dumps({"values": values, "mode": "install"}).encode()
        status, _, body = api.dispatch("POST", "/api/config/preview", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        data = json.loads(body.decode())
        self.assertIsNone(data["cnc_cnfg"])
        self.assertTrue(any(i["level"] == "ERROR" for i in data["issues"]))

    def test_save_install_writes_cnc_cnfg(self):
        sid = self._session()
        values = {"BOXTYPE": "SINGLE", "INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0",
                  "NEW_LOAD": "03", "DEPOTFILE": "/depot/x.rpm"}
        payload = json.dumps({"values": values, "mode": "install"}).encode()
        status, _, body = api.dispatch("POST", "/api/config/save", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        saved = json.loads(body.decode())["saved"]
        text = open(saved).read()
        cnc_path = os.path.join(self.ctx.inclogdir, "nfupgrader_cnc.cnfg")
        self.assertIn('CNCCNFG="{}"'.format(cnc_path), text)
        expected = cnccnfg.render_from_values(values, self.ctx.hostname)
        self.assertEqual(open(cnc_path).read(), expected)
        events = self._audit()
        rec = [r for r in events if r["event"] == "config_saved"][0]
        self.assertEqual(rec["mode"], "install")

    def test_save_install_invalid_hostname_writes_nothing(self):
        sid = self._session()
        ctx = self.ctx._replace(hostname="bad host")
        cnc_path = os.path.join(ctx.inclogdir, "nfupgrader_cnc.cnfg")
        open(cnc_path, "w").write("PRE-EXISTING\n")
        pre_cnc = open(cnc_path, "rb").read()
        saved_path = os.path.join(ctx.inclogdir, "nfupgrader_user.cfg")
        self.assertFalse(os.path.exists(saved_path))
        values = {"BOXTYPE": "SINGLE", "INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0",
                  "NEW_LOAD": "03", "DEPOTFILE": "/depot/x.rpm"}
        payload = json.dumps({"values": values, "mode": "install"}).encode()
        status, _, body = api.dispatch("POST", "/api/config/save", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", ctx)
        self.assertEqual(status, 400)
        data = json.loads(body.decode())
        self.assertEqual(data["error"], "config invalid")
        self.assertTrue(any(i["level"] == "ERROR" for i in data["issues"]))
        self.assertEqual(open(cnc_path, "rb").read(), pre_cnc)
        self.assertFalse(os.path.exists(saved_path))

    def test_save_upgrade_unchanged(self):
        sid = self._session()
        payload = json.dumps({"values": {
            "INCLOGDIR": "/usr/cnc/logs", "NEW_GENERIC": "5.5.0", "NEW_LOAD": "03",
            "DEPOTFILE": "/depot/netFLEX-CORE-5.5.0.03-1.x86_64.rpm"}}).encode()
        status, _, body = api.dispatch("POST", "/api/config/save", {}, payload,
                                       {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        saved = json.loads(body.decode())["saved"]
        self.assertEqual(saved, os.path.join(self.ctx.inclogdir, "nfupgrader_user.cfg"))
        events = self._audit()
        rec = [r for r in events if r["event"] == "config_saved"][0]
        self.assertEqual(rec["mode"], "upgrade")

    def test_launch_409_while_upgrader_running(self):
        self._set_running(True)
        sid = self._session()
        status, _, body = api.dispatch(
            "POST", "/api/launch", {}, json.dumps({"mode": "stage"}).encode(),
            {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 409)
        data = json.loads(body.decode())
        self.assertEqual(data, {"error": "upgrade already running"})
        self.assertNotIn("not_overridden", data)
        self.assertNotIn("launch", [r["event"] for r in self._audit()])

    def test_launch_bad_mode_400(self):
        sid = self._session()
        status, _, _ = api.dispatch(
            "POST", "/api/launch", {}, json.dumps({"mode": "x"}).encode(),
            {"nfu_sid": sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 400)

    def test_launch_non_object_json_400(self):
        sid = self._session()
        for raw in (b'["stage"]', b'"stage"', b"3", b"null"):
            status, _, body = api.dispatch("POST", "/api/launch", {}, raw,
                                           {"nfu_sid": sid}, "127.0.0.1", self.ctx)
            self.assertEqual((status, json.loads(body.decode())), (400, {"error": "bad json"}),
                             raw)


REPORTS_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "reports.test.json")


class TestReportsApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        base = make_ctx(self.tmp, running=False)
        base.host.responses[("sh", "-c", "echo v")] = RunResult(0, "v1\n", "")
        base.host.responses[("sh", "-c", "df")] = RunResult(0, "/usr4 10%\n", "")
        self.root = os.path.join(self.tmp, "nfupgrader_reports")
        ticks = iter(datetime.datetime(2026, 9, 23, 14, 0, s) for s in range(60))
        self.runner = snapjobs.SnapshotRunner(base.host, REPORTS_FIXTURE, self.root,
                                              audit=base.audit, spawn=lambda fn: fn(),
                                              now=lambda: next(ticks))
        self.ctx = base._replace(runner=self.runner, reports_root=self.root,
                                 reports_path=REPORTS_FIXTURE)
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")

    def _get(self, path, **q):
        return api.dispatch("GET", path, dict((k, [v]) for k, v in q.items()), b"",
                            {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)

    def _post(self, path, payload=None):
        return api.dispatch("POST", path, {}, json.dumps(payload or {}).encode(),
                            {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)

    def _json(self, resp):
        return json.loads(resp[2].decode())

    def test_requires_session(self):
        status, _, _ = api.dispatch("GET", "/api/reports", {}, b"", {}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 401)

    def test_run_then_list_and_idle_job(self):
        resp = self._post("/api/reports/run")
        self.assertEqual(resp[0], 202)
        sid = self._json(resp)["id"]
        snaps = self._json(self._get("/api/reports"))["snapshots"]
        self.assertEqual([(s["id"], s["label"], s["status"]) for s in snaps],
                         [(sid, "manual", "complete")])
        self.assertEqual(self._json(self._get("/api/reports/job")),
                         {"job": None, "launch_error": None, "notice": None,
                          "interrupted_fix": None})

    def test_busy_run_and_launch_409(self):
        held = []
        self.runner.spawn = held.append
        self.assertEqual(self._post("/api/reports/run")[0], 202)
        self.assertEqual(self._post("/api/reports/run")[0], 409)
        self.assertEqual(self._json(self._get("/api/reports/job"))["job"]["label"], "manual")
        resp = self._post("/api/launch", {"mode": "stage"})
        self.assertEqual(resp[0], 409)
        self.assertEqual(self._json(resp)["error"], "report snapshot in progress")
        held[0]()
        self.assertEqual(self._post("/api/launch", {"mode": "stage"})[0], 202)

    def _set_running(self, running):
        self.ctx.host.responses.update(running_responses(running))

    def test_run_409_while_upgrader_running(self):
        self._set_running(True)
        resp = self._post("/api/reports/run")
        self.assertEqual(resp[0], 409)
        self.assertEqual(self._json(resp), {"error": "upgrade in progress"})
        self.assertEqual(self._json(self._get("/api/reports"))["snapshots"], [])
        self._set_running(False)
        self.assertEqual(self._post("/api/reports/run")[0], 202)

    def test_launch_snapshot_busy_checked_before_upgrader(self):
        held = []
        self.runner.spawn = held.append
        self.assertEqual(self._post("/api/reports/run")[0], 202)
        self._set_running(True)
        resp = self._post("/api/launch", {"mode": "stage"})
        self.assertEqual((resp[0], self._json(resp)["error"]),
                         (409, "report snapshot in progress"))
        held[0]()
        resp = self._post("/api/launch", {"mode": "stage"})
        self.assertEqual((resp[0], self._json(resp)["error"]),
                         (409, "upgrade already running"))

    def test_launch_and_run_409_while_fix_in_progress(self):
        self.assertFalse(api.fix_in_progress())
        self.assertTrue(api._FIX_LOCK.acquire(False))
        try:
            self.assertTrue(api.fix_in_progress())
            launch = self._post("/api/launch", {"mode": "stage"})
            run = self._post("/api/reports/run")
        finally:
            api._FIX_LOCK.release()
        self.assertEqual((launch[0], self._json(launch)), (409, {"error": "check fix in progress"}))
        self.assertEqual((run[0], self._json(run)), (409, {"error": "check fix in progress"}))
        self.assertEqual(self._json(self._get("/api/reports"))["snapshots"], [])
        self.assertFalse(api.fix_in_progress())
        self.assertEqual(self._post("/api/launch", {"mode": "stage"})[0], 202)

    def test_compare_diff_raw(self):
        a = self._json(self._post("/api/reports/run"))["id"]
        self.ctx.host.responses[("sh", "-c", "echo v")] = RunResult(0, "v2\n", "")
        b = self._json(self._post("/api/reports/run"))["id"]
        cmp_ = self._json(self._get("/api/reports/compare", a=a, b=b))
        self.assertEqual([(r["name"], r["status"]) for r in cmp_["reports"]],
                         [("Version", "changed"), ("Disk Check", "unchanged")])
        side = self._json(self._get("/api/reports/diff", a=a, b=b, report="Version", style="side"))
        self.assertEqual(side["rows"], [{"op": "replace", "left": "v1", "right": "v2"}])
        uni = self._json(self._get("/api/reports/diff", a=a, b=b, report="Version",
                                   style="unified"))
        self.assertEqual([r["op"] for r in uni["rows"]], ["@", "-", "+"])
        status, headers, body = self._get("/api/reports/raw", id=a, report="Version")
        self.assertEqual(status, 200)
        self.assertIn(("Content-Type", "text/plain; charset=utf-8"), headers)
        self.assertIn(("X-Content-Type-Options", "nosniff"), headers)
        self.assertEqual(body, b"v1\n")

    def _interrupted_before(self):
        """A before snapshot whose job died mid-run (manifest still 'running',
        no live job) -- as left behind by a service restart."""
        sid = reports.new_snapshot_id(self.root, "before",
                                      datetime.datetime(2026, 9, 23, 15, 0, 0))
        os.makedirs(self.root, exist_ok=True)
        reports.take_snapshot(self.ctx.host, [], self.root, sid, "before", mode="upgrade")
        m = reports.read_manifest(self.root, sid)
        m["status"] = "running"
        reports._write_manifest(os.path.join(self.root, sid), m)
        return sid

    def test_reports_job_notice_for_interrupted_before(self):
        sid = self._interrupted_before()
        d = self._json(self._get("/api/reports/job"))
        self.assertIsNone(d["job"])
        self.assertEqual(d["notice"], "Before snapshot {} was interrupted \u2014 "
                                      "the upgrade was not launched.".format(sid))

    def test_reports_job_no_notice_while_upgrader_running(self):
        self._interrupted_before()
        self._set_running(True)
        self.assertIsNone(self._json(self._get("/api/reports/job"))["notice"])

    def test_reports_job_no_notice_for_complete_before_or_manual(self):
        self._post("/api/reports/run")
        self.assertIsNone(self._json(self._get("/api/reports/job"))["notice"])
        snapjobs.begin_upgrade(self.runner, lambda: None, lambda: "done", client="ip",
                               spawn=lambda fn: None)
        self.assertIsNone(self._json(self._get("/api/reports/job"))["notice"])

    def test_reports_job_no_notice_while_job_running(self):
        sid = self._interrupted_before()
        held = []
        self.runner.spawn = held.append
        self.runner.start("manual")
        d = self._json(self._get("/api/reports/job"))
        self.assertEqual(d["job"]["label"], "manual")
        self.assertIsNone(d["notice"])

    def test_reports_job_surfaces_launch_error(self):
        self.ctx.state["launch_error"] = "no nf_upgrade"
        self.assertEqual(self._json(self._get("/api/reports/job"))["launch_error"],
                         "no nf_upgrade")

    def test_reports_job_interrupted_fix_null_by_default(self):
        self.assertIsNone(self._json(self._get("/api/reports/job"))["interrupted_fix"])

    def test_reports_job_surfaces_interrupted_fix(self):
        self.ctx.state["interrupted_fix"] = {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"}
        self.assertEqual(self._json(self._get("/api/reports/job"))["interrupted_fix"],
                         {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"})

    def test_errors(self):
        a = self._json(self._post("/api/reports/run"))["id"]
        bad = "20990101_000000_after"
        self.assertEqual(self._get("/api/reports/compare", a=a, b=bad)[0], 404)
        self.assertEqual(self._get("/api/reports/diff", a=a, b=bad, report="Version")[0], 404)
        self.assertEqual(self._get("/api/reports/diff", a=a, b=a, report="Nope")[0], 404)
        self.assertEqual(self._get("/api/reports/diff", a=a, b=a, report="Version",
                                   style="weird")[0], 400)
        self.assertEqual(self._get("/api/reports/raw", id="../x", report="Version")[0], 404)
        self.assertEqual(self._get("/api/reports/raw", id=a, report="Nope")[0], 404)

    def test_no_runner(self):
        ctx = make_ctx(tempfile.mkdtemp())
        sid = ctx.auth.redeem("TOK", "127.0.0.1")
        status, _, body = api.dispatch("POST", "/api/reports/run", {}, b"{}",
                                       {"nfu_sid": sid}, "127.0.0.1", ctx)
        self.assertEqual(status, 503)
        status, _, body = api.dispatch("GET", "/api/reports", {}, b"",
                                       {"nfu_sid": sid}, "127.0.0.1", ctx)
        self.assertEqual(json.loads(body.decode()), {"snapshots": []})



class TestActivePhase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def ctx(self, **kw):
        # make_ctx writes its own state file; tests set theirs afterwards.
        c = make_ctx(self.tmp, running=False)._replace(**kw)
        self.sids = getattr(self, "sids", {})
        self.sids[id(c)] = c.auth.redeem("TOK", "127.0.0.1")
        return c

    def get(self, ctx, path, **q):
        sid = self.sids[id(ctx)]
        status, _, body = api.dispatch("GET", path, dict((k, [v]) for k, v in q.items()), b"",
                                       {"nfu_sid": sid}, "127.0.0.1", ctx)
        return json.loads(body.decode())

    def state(self, value):
        open(os.path.join(self.tmp, ".h1_STATE"), "w").write(value + "\n")

    def test_inferred_from_state_file_when_unset(self):
        ctx = self.ctx(phase=None)
        self.state("swistart")
        self.assertEqual(self.get(ctx, "/api/progress")["phase"], "upgrade")
        self.state("swscomplete")
        self.assertEqual(self.get(ctx, "/api/progress")["phase"], "stage")

    def test_env_phase_beats_inference(self):
        ctx = self.ctx(phase="stage")
        self.state("swistart")
        self.assertEqual(self.get(ctx, "/api/progress")["phase"], "stage")

    def test_last_launch_beats_env(self):
        ctx = self.ctx(phase="stage", state={"phase": "upgrade"})
        self.state("swscomplete")
        p = self.get(ctx, "/api/progress")
        self.assertEqual((p["phase"], p["status"]), ("upgrade", "not_started"))

    def test_log_follows_newest_trace_and_signals_reset(self):
        open(os.path.join(self.tmp, "stage_inc_h1.10"), "w").write(
            "2026-09-23 16:00:00 INFO  first run\n")
        ctx = self.ctx(phase=None, tracefile=None)
        d = self.get(ctx, "/api/log", offset="0")
        self.assertEqual(d["file"], "stage_inc_h1.10")
        self.assertFalse(d["reset"])
        self.assertEqual([e["msg"] for e in d["entries"]], ["first run"])
        newer = os.path.join(self.tmp, "stage_inc_h1.20")
        open(newer, "w").write("2026-09-23 17:00:00 INFO  second run\n")
        os.utime(newer, None)
        os.utime(os.path.join(self.tmp, "stage_inc_h1.10"), (1, 1))
        d = self.get(ctx, "/api/log", offset=str(d["next_offset"]), file="stage_inc_h1.10")
        self.assertEqual(d["file"], "stage_inc_h1.20")
        self.assertTrue(d["reset"])
        self.assertEqual([e["msg"] for e in d["entries"]], ["second run"])

    def test_log_reports_full_path_and_existence(self):
        ctx = self.ctx(phase=None, tracefile=None)
        d = self.get(ctx, "/api/log", offset="0")
        self.assertEqual(d["path"], os.path.join(self.tmp, "stage_inc_h1"))   # nothing yet
        self.assertFalse(d["exists"])
        open(os.path.join(self.tmp, "stage_inc_h1.42"), "w").write("x\n")
        d = self.get(ctx, "/api/log", offset="0")
        self.assertEqual((d["path"], d["exists"]), (os.path.join(self.tmp, "stage_inc_h1.42"), True))

    def test_explicit_tracefile_still_wins(self):
        d = self.get(self.ctx(), "/api/log", offset="0")
        self.assertEqual(d["file"], "trace")


FIX_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "checks.fix.json")


class InstallingHost(FakeHost):
    """rpm -q ksh fails until `dnf install -y ksh` has run."""

    def run(self, argv, timeout=None):
        if list(argv) == ["dnf", "install", "-y", "ksh"]:
            self.responses[("rpm", "-q", "ksh")] = RunResult(0, "ksh-1\n", "")
        return FakeHost.run(self, argv, timeout)


class TestCheckFix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        base = make_ctx(self.tmp, running=False)
        host = InstallingHost(dict(base.host.responses))
        host.responses[("rpm", "-q", "ksh")] = RunResult(1, "package ksh is not installed\n", "")
        host.responses[("dnf", "install", "-y", "ksh")] = RunResult(0, "Complete!\n", "")
        self.ctx = base._replace(host=host, checks_path=FIX_FIXTURE, phase="stage")
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")
        # dnf "exists" regardless of this machine (RHEL 7 would fall back to yum).
        self._dnf = checks.DNF
        checks.DNF = os.path.join(self.tmp, "dnf")
        open(checks.DNF, "w").close()

    def tearDown(self):
        checks.DNF = self._dnf

    def fix(self, payload, cookies=None):
        status, _, body = api.dispatch(
            "POST", "/api/checks/fix", {}, json.dumps(payload).encode(),
            {"nfu_sid": self.sid} if cookies is None else cookies, "127.0.0.1", self.ctx)
        return status, json.loads(body.decode())

    def events(self):
        path = os.path.join(self.tmp, "audit.jsonl")
        return [json.loads(l) for l in open(path)] if os.path.exists(path) else []

    def test_requires_session(self):
        self.assertEqual(self.fix({"name": "pkg-ksh"}, cookies={})[0], 401)

    def test_listing_offers_fix_for_failed_fixable_check(self):
        status, _, body = api.dispatch("GET", "/api/checks",
                                       {"when": ["preflight"], "phase": ["stage"]}, b"",
                                       {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        res = dict((r["name"], r) for r in json.loads(body.decode())["results"])
        self.assertEqual(res["pkg-ksh"]["fix"], "dnf install -y ksh")
        self.assertIsNone(res["sdi-mode"]["fix"])

    def test_fix_runs_audits_and_rechecks(self):
        status, d = self.fix({"name": "pkg-ksh", "phase": "stage"})
        self.assertEqual(status, 200)
        self.assertEqual(d["fix"], {"ok": True, "detail": "ksh installed"})
        self.assertTrue(d["result"]["passed"])
        ev = [(e["event"], e.get("name")) for e in self.events()]
        self.assertIn(("check_fix_start", "pkg-ksh"), ev)
        self.assertIn(("check_fix", "pkg-ksh"), ev)
        done = [e for e in self.events() if e["event"] == "check_fix"][0]
        self.assertTrue(done["ok"])

    def test_already_passing_runs_nothing(self):
        self.ctx.host.responses[("rpm", "-q", "ksh")] = RunResult(0, "ksh-1\n", "")
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)
        self.assertIsNone(d["fix"])
        self.assertNotIn(["dnf", "install", "-y", "ksh"], self.ctx.host.calls)
        self.assertNotIn("check_fix_start", [e["event"] for e in self.events()])

    def test_refusals(self):
        self.assertEqual(self.fix({"name": "nope"})[0], 404)
        self.assertEqual(self.fix({"name": "upgrade-only", "phase": "stage"})[0], 404)
        self.assertEqual(self.fix({"name": "sdi-mode"})[0], 400)
        self.assertEqual(self.fix({"name": "pkg-ksh", "phase": "weird"})[0], 400)

    def test_blocked_while_upgrade_running(self):
        self.ctx.host.responses.update(running_responses(True))
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual((status, d["error"]), (409, "upgrade in progress"))

    def test_blocked_while_snapshot_busy(self):
        class BusyRunner(object):
            def busy(self):
                return True
        self.ctx = self.ctx._replace(runner=BusyRunner())
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual((status, d["error"]), (409, "report snapshot in progress"))

    def raw_fix(self, raw):
        status, _, body = api.dispatch("POST", "/api/checks/fix", {}, raw,
                                       {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        return status, json.loads(body.decode())

    def test_non_object_json_400(self):
        for raw in (b"[1]", b'"pkg-ksh"', b"7", b"null", b"{", b"\xff"):
            self.assertEqual(self.raw_fix(raw), (400, {"error": "bad json"}), raw)
        self.assertFalse(api._FIX_LOCK.locked())

    def test_lock_released_after_every_outcome(self):
        cases = [({"name": "nope"}, 404), ({"name": "sdi-mode"}, 400),
                 ({"name": "pkg-ksh", "phase": "weird"}, 400), ({"name": "pkg-ksh"}, 200),
                 ({"name": "pkg-ksh"}, 200)]          # second: already passing
        for payload, want in cases:
            self.assertEqual(self.fix(payload)[0], want, payload)
            self.assertFalse(api._FIX_LOCK.locked(), payload)
        self.ctx.host.responses.update(running_responses(True))
        self.assertEqual(self.fix({"name": "pkg-ksh"})[0], 409)
        self.assertFalse(api._FIX_LOCK.locked())

    def test_lock_held_while_checking_state_and_fixing(self):
        seen = []

        class Watching(InstallingHost):
            def run(self, argv, timeout=None):
                seen.append((list(argv), api.fix_in_progress()))
                return InstallingHost.run(self, argv, timeout)
        self.ctx = self.ctx._replace(host=Watching(dict(self.ctx.host.responses)))
        self.assertEqual(self.fix({"name": "pkg-ksh"})[0], 200)
        self.assertTrue(seen)
        # Every command, including the upgrader_running probe, ran under the lock.
        self.assertEqual([argv for argv, held in seen if not held], [])
        self.assertIn(["pgrep", "-f", "nf_upgrader"], [argv for argv, _ in seen])

    def test_lock_checked_before_upgrade_running(self):
        self.ctx.host.responses.update(running_responses(True))
        self.assertTrue(api._FIX_LOCK.acquire(False))
        try:
            status, d = self.fix({"name": "pkg-ksh"})
        finally:
            api._FIX_LOCK.release()
        self.assertEqual((status, d["error"]), (409, "another fix is running"))

    def test_one_fix_at_a_time(self):
        self.assertTrue(api._FIX_LOCK.acquire(False))
        try:
            status, d = self.fix({"name": "pkg-ksh"})
        finally:
            api._FIX_LOCK.release()
        self.assertEqual((status, d["error"]), (409, "another fix is running"))

    def test_refused_just_after_launch(self):
        self.ctx.state["launched_at"] = api._now() - 1
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual((status, d), (409, {"error": "upgrade just launched"}))
        self.assertFalse(api._FIX_LOCK.locked())

    def test_proceeds_once_grace_period_elapses(self):
        self.ctx.state["launched_at"] = api._now() - api.FIX_LAUNCH_GRACE - 1
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)

    def test_unaffected_when_never_launched(self):
        self.assertNotIn("launched_at", self.ctx.state)
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)

    def test_grace_period_uses_injectable_clock(self):
        self.ctx.state["launched_at"] = 1000.0
        orig_now = api._now
        api._now = lambda: 1000.0 + api.FIX_LAUNCH_GRACE - 1
        try:
            status, d = self.fix({"name": "pkg-ksh"})
        finally:
            api._now = orig_now
        self.assertEqual((status, d), (409, {"error": "upgrade just launched"}))

    def test_fix_clears_interrupted_fix_for_same_name(self):
        self.ctx.state["interrupted_fix"] = {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"}
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)
        self.assertIsNone(self.ctx.state["interrupted_fix"])

    def test_fix_leaves_interrupted_fix_for_other_name(self):
        self.ctx.state["interrupted_fix"] = {"name": "sdi-mode", "kind": "cfg", "ts": "t1"}
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)
        self.assertEqual(self.ctx.state["interrupted_fix"],
                         {"name": "sdi-mode", "kind": "cfg", "ts": "t1"})

    def test_fix_failure_also_clears_interrupted_fix(self):
        self.ctx.host.responses[("dnf", "install", "-y", "ksh")] = RunResult(1, "", "boom")
        self.ctx.state["interrupted_fix"] = {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"}
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)
        self.assertFalse(d["fix"]["ok"])
        self.assertIsNone(self.ctx.state["interrupted_fix"])

    def test_already_passing_leaves_interrupted_fix_untouched(self):
        self.ctx.host.responses[("rpm", "-q", "ksh")] = RunResult(0, "ksh-1\n", "")
        self.ctx.state["interrupted_fix"] = {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"}
        status, d = self.fix({"name": "pkg-ksh"})
        self.assertEqual(status, 200)
        self.assertIsNone(d["fix"])
        self.assertEqual(self.ctx.state["interrupted_fix"],
                         {"name": "pkg-ksh", "kind": "rpm", "ts": "t1"})



class TestUpgradeLogdir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()           # nfupgrader's own INCLOGDIR
        self.updir = tempfile.mkdtemp()         # where the upgrade scripts log
        self.ctx = make_ctx(self.tmp, running=False)._replace(phase=None, tracefile=None)
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")

    def cfg(self, text):
        path = os.path.join(self.tmp, "my.cfg")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def get(self, path, **q):
        status, _, body = api.dispatch("GET", path, dict((k, [v]) for k, v in q.items()), b"",
                                       {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        return json.loads(body.decode())

    def test_defaults_to_own_inclogdir(self):
        self.assertEqual(api.upgrade_logdir(self.ctx), self.tmp)

    def test_uses_inclogdir_from_saved_config_then_env_config(self):
        saved = self.cfg('INCLOGDIR="%s"\nNEW_GENERIC=5.4.1\n' % self.updir)
        self.ctx.state["user_config"] = saved
        self.assertEqual(api.upgrade_logdir(self.ctx), self.updir)
        del self.ctx.state["user_config"]
        ctx = self.ctx._replace(user_config=saved)
        self.assertEqual(api.upgrade_logdir(ctx), self.updir)

    def test_falls_back_when_unusable(self):
        for text in ("NEW_GENERIC=5.4.1\n", "INCLOGDIR=${BASE}/logs\n", "INCLOGDIR=logs\n",
                     "INCLOGDIR=\n"):
            self.ctx.state["user_config"] = self.cfg(text)
            self.assertEqual(api.upgrade_logdir(self.ctx), self.tmp, text)
        self.ctx.state["user_config"] = os.path.join(self.tmp, "missing.cfg")
        self.assertEqual(api.upgrade_logdir(self.ctx), self.tmp)

    def test_progress_log_and_info_follow_the_config_logdir(self):
        host = self.ctx.hostname
        open(os.path.join(self.updir, ".%s_STATE" % host), "w").write("swscopycnc\n")
        open(os.path.join(self.updir, "stage_inc_%s.77" % host), "w").write(
            "2026-09-24 17:37:05 INFO  Staging holmvm32\n")
        self.ctx.state["user_config"] = self.cfg("INCLOGDIR=%s\n" % self.updir)
        p = self.get("/api/progress")
        self.assertEqual((p["phase"], p["current"], p["logdir"]), ("stage", "swscopycnc", self.updir))
        log = self.get("/api/log", offset="0")
        self.assertEqual(log["file"], "stage_inc_%s.77" % host)
        self.assertEqual([e["msg"] for e in log["entries"]], ["Staging holmvm32"])
        info = self.get("/api/info")
        self.assertEqual(info["upgrade_logdir"], self.updir)
        self.assertEqual(info["nfupgrader_logdir"], self.tmp)


INSTALL_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "checks.install.json")


def write_install_cfg(tmp, **over):
    """Write a saved install config into tmp (INCLOGDIR=tmp, a readable depot
    RPM and cnc.cnfg) with KEY=value overrides (None drops the key); return its path."""
    depot = os.path.join(tmp, "netFLEX-CORE-5.3.0.02-1.x86_64.rpm")
    open(depot, "w").close()
    vals = collections.OrderedDict([
        ("INCLOGDIR", tmp), ("NEW_GENERIC", "5.3.0"), ("NEW_LOAD", "02"),
        ("DEPOTFILE", depot), ("CNCCNFG", os.path.join(tmp, "cnc.cnfg"))])
    for k, v in over.items():
        if v is None:
            vals.pop(k, None)
        else:
            vals[k] = v
    path = os.path.join(tmp, "nfupgrader_user.cfg")
    with open(path, "w") as fh:
        fh.write("".join("{}={}\n".format(k, v) for k, v in vals.items()))
    return path


def install_ctx(tmp, installed=False, state=None, running=False, config=True):
    """A Context modelling a host mid new-install (or not yet installed): built
    from make_ctx (nf_upgrader idle), with the install-mode check fixture, and
    optionally an installed netFLEX-CORE, an install state file, a live
    nf_install process, and (by default) a valid saved install config."""
    ctx = make_ctx(tmp, running=False)
    if config:
        ctx = ctx._replace(user_config=write_install_cfg(tmp))
    if not installed:
        # Not installed: rpm says absent; the /usr/cnc existence probe is left
        # unmocked, so FakeHost's default (rc 1, "not present") applies too.
        ctx.host.responses[("rpm", "-q", "netFLEX-CORE")] = RunResult(1, "", "")
    if running:
        ctx.host.responses.update({
            ("pgrep", "-f", "nf_install"): RunResult(0, "5252\n", ""),
            ("cat", "/proc/5252/cmdline"):
                RunResult(0, "/usr/bin/ksh\0/opt/nfupgrader/nf_install\0-i\0", ""),
        })
    if state is not None:
        open(os.path.join(tmp, ".h1_INSTALL_STATE"), "w").write(state + "\n")
    return ctx._replace(checks_path=INSTALL_FIXTURE)


class TestInstallApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sids = {}

    def _sid(self, ctx):
        # The redemption token is single-use; cache the session per ctx so a
        # test can make several calls against the same context.
        if id(ctx) not in self.sids:
            self.sids[id(ctx)] = ctx.auth.redeem("TOK", "127.0.0.1")
        return self.sids[id(ctx)]

    def _get(self, ctx, path, **q):
        status, _, body = api.dispatch("GET", path, dict((k, [v]) for k, v in q.items()), b"",
                                       {"nfu_sid": self._sid(ctx)}, "127.0.0.1", ctx)
        return status, json.loads(body.decode())

    def _post(self, ctx, path, payload):
        status, _, body = api.dispatch("POST", path, {}, json.dumps(payload).encode(),
                                       {"nfu_sid": self._sid(ctx)}, "127.0.0.1", ctx)
        return status, json.loads(body.decode())

    def test_status_fresh_box(self):
        ctx = install_ctx(self.tmp)
        status, data = self._get(ctx, "/api/install/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "install")
        self.assertFalse(data["installed"])
        self.assertFalse(data["unfinished"])

    def test_active_phase_install_from_state_file_after_restart(self):
        ctx = install_ctx(self.tmp, state="isrpm", running=True)
        self.assertEqual(ctx.state, {})
        status, data = self._get(ctx, "/api/progress")
        self.assertEqual(status, 200)
        self.assertEqual((data["phase"], data["current"], data["status"]),
                         ("install", "isrpm", "running"))

    def test_checks_install_excludes_stage_upgrade(self):
        ctx = install_ctx(self.tmp)
        status, data = self._get(ctx, "/api/checks", when="preflight", phase="install")
        self.assertEqual(status, 200)
        byname = dict((r["name"], r) for r in data["results"])
        self.assertNotIn("stage-or-upgrade-only", byname)
        self.assertIn("netflex-absent", byname)
        self.assertTrue(byname["netflex-absent"]["hard"])

    def test_checks_postflight_install_uses_install_postflight(self):
        ctx = install_ctx(self.tmp, installed=True)
        status, data = self._get(ctx, "/api/checks", when="postflight", phase="install")
        self.assertEqual(status, 200)
        self.assertEqual([r["name"] for r in data["results"]], ["pkg-ksh"])

    def test_launch_install_ok(self):
        ctx = install_ctx(self.tmp)
        status, data = self._post(ctx, "/api/launch", {"mode": "install"})
        self.assertEqual(status, 202)
        self.assertEqual(data, {"accepted": True, "mode": "install", "action": "install"})

    def test_launch_install_refused_when_installed(self):
        ctx = install_ctx(self.tmp, installed=True)
        ctx.state["phase"] = "install"
        for payload in ({"mode": "install"},
                        {"mode": "install", "overrides": ["netflex-absent"]}):
            status, data = self._post(ctx, "/api/launch", payload)
            self.assertEqual(status, 409, payload)
            self.assertEqual(data["error"], "netFLEX is already installed")

    def test_launch_hard_failure_never_overridable(self):
        # F2: host_mode says install (no rpm, no /usr/cnc), so the launch reaches
        # the hard_failures branch: netflex-absent fails on a leftover
        # /usr/cnc_stage (R9) and stays a stop even when named with a reason.
        ctx = install_ctx(self.tmp)
        ctx.host.responses[("sh", "-c", "test -e /usr/cnc_stage || test -L /usr/cnc_stage")] = \
            RunResult(0, "", "")
        for payload in ({"mode": "install"},
                        {"mode": "install", "overrides": ["netflex-absent"], "reason": "go"}):
            status, data = self._post(ctx, "/api/launch", payload)
            self.assertEqual(status, 409, payload)
            self.assertEqual([h["name"] for h in data["hard"]], ["netflex-absent"])
            self.assertEqual(data["hard"][0]["detail"], "/usr/cnc_stage exists")
            self.assertNotIn("not_overridden", data)
        self.assertNotIn("launch", self._events())
        self.assertNotIn("override", self._events())

    def _events(self):
        try:
            with open(os.path.join(self.tmp, "audit.jsonl")) as fh:
                return [json.loads(l)["event"] for l in fh.read().splitlines()]
        except (IOError, OSError):
            return []

    def _refused(self, ctx, needle):
        status, data = self._post(ctx, "/api/launch", {"mode": "install"})
        self.assertEqual(status, 409, data)
        self.assertIn(needle, data["error"])
        self.assertNotIn("launch", self._events())

    def test_launch_install_requires_saved_config(self):
        ctx = install_ctx(self.tmp, config=False)
        self._refused(ctx, "save an install configuration on the Config tab first")

    def test_launch_resume_requires_saved_config(self):
        ctx = install_ctx(self.tmp, installed=True, state="ispostinstall", config=False)
        self._refused(ctx, "save an install configuration on the Config tab first")

    def test_launch_install_config_file_missing(self):
        ctx = install_ctx(self.tmp, config=False)._replace(
            user_config=os.path.join(self.tmp, "gone.cfg"))
        self._refused(ctx, "cannot read")

    def _refused_with(self, needle, **over):
        # One ctx per test (the redemption token is single-use); the saved
        # config in ctx.state wins over ctx.user_config, like a Config-tab save.
        if not hasattr(self, "_cfg_ctx"):
            self._cfg_ctx = install_ctx(self.tmp, config=False)
        self._cfg_ctx.state["user_config"] = write_install_cfg(self.tmp, **over)
        self._refused(self._cfg_ctx, needle)

    def test_launch_install_depotfile_checked(self):
        self._refused_with("DEPOTFILE", DEPOTFILE=None)
        self._refused_with("DEPOTFILE", DEPOTFILE=os.path.join(self.tmp, "missing.rpm"))
        self._refused_with("DEPOTFILE", DEPOTFILE=self.tmp)   # a directory

    def test_launch_install_cnccnfg_checked(self):
        self._refused_with("CNCCNFG", CNCCNFG=None)
        self._refused_with("CNCCNFG", CNCCNFG=os.path.join(self.tmp, "missing.cnfg"))

    def test_launch_install_inclogdir_checked(self):
        self._refused_with("INCLOGDIR", INCLOGDIR=None)
        self._refused_with("INCLOGDIR", INCLOGDIR="logs")
        self._refused_with("INCLOGDIR", INCLOGDIR="$HOME/logs")
        self._refused_with("INCLOGDIR", INCLOGDIR=os.path.join(self.tmp, "nodir"))
        self._refused_with("/usr/cnc", INCLOGDIR="/usr/cnc/logs")
        self._refused_with("/usr/cnc", INCLOGDIR="/usr/cnc")
        self._refused_with("/usr/cnc", INCLOGDIR="/usr/cnc/../cnc/logs")
        # and a valid one is accepted on the same ctx
        self._cfg_ctx.state["user_config"] = write_install_cfg(self.tmp)
        status, data = self._post(self._cfg_ctx, "/api/launch", {"mode": "install"})
        self.assertEqual(status, 202, data)

    def test_launch_install_inclogdir_not_writable(self):
        orig = os.access
        def fake_access(path, mode):
            if os.path.realpath(path) == os.path.realpath(self.tmp) and mode == os.W_OK:
                return False
            return orig(path, mode)
        ctx = install_ctx(self.tmp)
        api.os.access = fake_access
        try:
            self._refused(ctx, "INCLOGDIR")
        finally:
            api.os.access = orig

    def test_status_computes_installed_once(self):
        ctx = install_ctx(self.tmp)
        status, data = self._get(ctx, "/api/install/status")
        self.assertEqual(status, 200)
        self.assertEqual((data["mode"], data["installed"]), ("install", False))
        self.assertEqual(ctx.host.calls.count(["rpm", "-q", "netFLEX-CORE"]), 1)
        ctx2 = install_ctx(tempfile.mkdtemp(), installed=True, state="isrpm")
        status, data = self._get(ctx2, "/api/install/status")
        self.assertEqual((data["mode"], data["installed"], data["unfinished"]),
                         ("install", True, True))
        ctx3 = install_ctx(tempfile.mkdtemp(), installed=True)
        status, data = self._get(ctx3, "/api/install/status")
        self.assertEqual((data["mode"], data["installed"]), ("upgrade", True))

    def test_launch_resume_skips_hard(self):
        ctx = install_ctx(self.tmp, installed=True, state="ispostinstall")
        status, data = self._post(ctx, "/api/launch", {"mode": "install"})
        self.assertEqual(status, 202, data)
        self.assertEqual(data["action"], "resume")

    def test_launch_stage_refused_when_not_installed(self):
        ctx = install_ctx(self.tmp)
        status, data = self._post(ctx, "/api/launch", {"mode": "stage"})
        self.assertEqual(status, 409)
        self.assertEqual(data["error"], "netFLEX is not installed")

    def test_reset_requires_reason_and_unfinished(self):
        ctx = install_ctx(self.tmp)
        status, data = self._post(ctx, "/api/install/reset", {"reason": "go"})
        self.assertEqual(status, 409)

        tmp2 = tempfile.mkdtemp()
        ctx2 = install_ctx(tmp2, state="isrpm")
        status, data = self._post(ctx2, "/api/install/reset", {"reason": ""})
        self.assertEqual(status, 400)
        status, data = self._post(ctx2, "/api/install/reset", {"reason": "cleanup"})
        self.assertEqual(status, 202)
        self.assertEqual(data, {"accepted": True, "action": "reset"})
        events = [json.loads(l)["event"] for l in
                  open(os.path.join(tmp2, "audit.jsonl")).read().splitlines()]
        self.assertIn("install_reset", events)

    def test_reset_refused_while_installing(self):
        ctx = install_ctx(self.tmp, state="isrpm", running=True)
        status, data = self._post(ctx, "/api/install/reset", {"reason": "go"})
        self.assertEqual(status, 409)

    def test_status_reports_fatal_lines(self):
        ctx = install_ctx(self.tmp, state="isrpm")
        open(os.path.join(self.tmp, "install_inc_h1.123"), "w").write(
            "2026-10-19 10:00:00 ERROR bad thing\n"
            "2026-10-19 10:00:01 FATAL worse thing\n")
        status, data = self._get(ctx, "/api/install/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["fatal"], ["bad thing", "worse thing"])


if __name__ == "__main__":
    unittest.main()

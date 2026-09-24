import datetime
import json
import os
import tempfile
import unittest
from nfupgrader import api, checks
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
        self.assertTrue(any(f["name"] == "NEW_GENERIC" for f in json.loads(body.decode())["fields"]))

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


if __name__ == "__main__":
    unittest.main()

"""api.py — pure request dispatcher. Holds routing, auth gating, and JSON
shaping so the HTTP layer (httpd.py) stays a thin adapter and the API is
unit-testable without sockets."""
import collections
import json

import os

from nfupgrader import progress, logtail, launcher, localinfo, siteview, site, checks, cfg
from nfupgrader import reports, snapjobs

Context = collections.namedtuple("Context", [
    "host", "hostname", "inclogdir", "phase", "tracefile",
    "auth", "audit", "webdir", "nf_upgrade_path",
    "effective_config", "user_config", "launch_cwd", "cnc_cnfg_path",
    "peer_host_factory", "checks_path", "state", "depot_dirs",
    "reports_path", "reports_root", "runner",
])
# The trailing fields are only used by side effects (launch), info gathering,
# checks, the config builder, or reports; default them so the dispatcher/tests
# need not supply each one. (namedtuple field defaults via __new__.__defaults__ — py3.6.)
Context.__new__.__defaults__ = (None,) * 11


def _default_peer_factory(name):
    from nfupgrader.host import SshHost
    return SshHost(name)


def _machtype(ctx):
    hosts = site.read_site(ctx.cnc_cnfg_path or "/usr/cnc/features/cnc.cnfg")
    me = site.local_host(hosts, ctx.hostname)
    return me.role if me else None


def _load_checks(ctx):
    path = ctx.checks_path or os.path.join(os.path.dirname(__file__), "checks.default.json")
    try:
        return checks.load_checks(path)
    except (IOError, OSError, ValueError):
        return []


def _json(status, obj):
    body = json.dumps(obj).encode("utf-8")
    return status, [("Content-Type", "application/json")], body


def _needs_session(cookies, client_ip, ctx):
    sid = cookies.get("nfu_sid")
    return bool(sid) and ctx.auth.valid_session(sid, client_ip)


def _q(query, name, default=""):
    return (query.get(name) or [default])[0]


def _reports_root(ctx):
    return ctx.reports_root or os.path.join(ctx.inclogdir, "nfupgrader_reports")


def _snapshot_busy(ctx):
    return ctx.runner is not None and ctx.runner.busy()


def dispatch(method, path, query, body, cookies, client_ip, ctx):
    """Return (status, headers, body_bytes) for a request."""
    # --- auth redemption (no session required) ---
    if path == "/auth" and method == "GET":
        token = (query.get("token") or [""])[0]
        sid = ctx.auth.redeem(token, client_ip)
        if not sid:
            ctx.audit.record("auth_fail", client=client_ip)
            return _json(401, {"error": "invalid or spent token"})
        ctx.audit.record("auth_ok", client=client_ip)
        headers = [
            ("Set-Cookie", "nfu_sid={}; HttpOnly; Path=/; SameSite=Strict".format(sid)),
            ("Location", "/"),
        ]
        return 303, headers, b""

    # --- everything else requires a valid session ---
    if not _needs_session(cookies, client_ip, ctx):
        return _json(401, {"error": "authentication required"})

    if path == "/api/progress" and method == "GET":
        running = launcher.upgrader_running(ctx.host)
        rec = progress.read_progress(ctx.hostname, ctx.phase, ctx.inclogdir, running)
        return _json(200, rec)

    if path == "/api/log" and method == "GET":
        offset = int((query.get("offset") or ["0"])[0])
        min_sev = (query.get("min") or [None])[0]
        chunk = logtail.tail_since(ctx.tracefile, offset, min_severity=min_sev)
        return _json(200, chunk)

    if path == "/api/info" and method == "GET":
        info = localinfo.gather(ctx.host, ctx.hostname,
                                ctx.cnc_cnfg_path or "/usr/cnc/features/cnc.cnfg")
        return _json(200, info)

    if path == "/api/site" and method == "GET":
        cnfg = ctx.cnc_cnfg_path or "/usr/cnc/features/cnc.cnfg"
        hosts = site.read_site(cnfg)
        factory = ctx.peer_host_factory or _default_peer_factory
        view = siteview.gather_site(hosts, ctx.hostname, cnfg, ctx.host, factory)
        return _json(200, view)

    if path == "/api/checks" and method == "GET":
        when = (query.get("when") or ["preflight"])[0]
        phase = (query.get("phase") or [ctx.phase])[0]
        results = checks.run_checks(ctx.host, _load_checks(ctx), when, phase, _machtype(ctx))
        return _json(200, {"results": results, "blocking": checks.blocking_failures(results)})

    if path == "/api/config/fields" and method == "GET":
        return _json(200, {"fields": cfg.FIELDS})

    if path == "/api/config/depots" and method == "GET":
        depots = cfg.list_depots(ctx.host, ctx.depot_dirs or ["/opt/apt/upgrade", "."])
        return _json(200, {"depots": depots})

    if path == "/api/config/preview" and method == "POST":
        try:
            values = json.loads(body.decode("utf-8") or "{}").get("values", {})
        except ValueError:
            return _json(400, {"error": "bad json"})
        return _json(200, {"issues": cfg.validate(values), "rendered": cfg.render_cfg(values)})

    if path == "/api/config/import" and method == "GET":
        p = (query.get("path") or [""])[0]
        try:
            with open(p) as fh:
                text = fh.read()
        except (IOError, OSError):
            return _json(404, {"error": "cannot read {}".format(p)})
        values = cfg.parse_cfg(text)
        forces = values.get("PROMPTFORVALIDATE", "NO") != "NO"
        return _json(200, {"values": values, "forces_promptforvalidate": forces,
                           "issues": cfg.validate(values)})

    if path == "/api/config/save" and method == "POST":
        try:
            values = json.loads(body.decode("utf-8") or "{}").get("values", {})
        except ValueError:
            return _json(400, {"error": "bad json"})
        issues = cfg.validate(values)
        if any(i["level"] == "ERROR" for i in issues):
            return _json(400, {"error": "config invalid", "issues": issues})
        saved = os.path.join(ctx.inclogdir, "nfupgrader_user.cfg")
        with open(saved, "w") as fh:
            fh.write(cfg.render_cfg(values))
        if ctx.state is not None:
            ctx.state["user_config"] = saved
        ctx.audit.record("config_saved", path=saved, client=client_ip)
        return _json(200, {"saved": saved})

    if path == "/api/reports" and method == "GET":
        live = ctx.runner.live_id() if ctx.runner is not None else None
        return _json(200, {"snapshots": reports.list_snapshots(_reports_root(ctx), live)})

    if path == "/api/reports/job" and method == "GET":
        return _json(200, {"job": ctx.runner.job() if ctx.runner is not None else None})

    if path == "/api/reports/run" and method == "POST":
        if ctx.runner is None:
            return _json(503, {"error": "reports unavailable"})
        try:
            snap_id = ctx.runner.start("manual", client=client_ip)
        except snapjobs.Busy:
            return _json(409, {"error": "report snapshot in progress"})
        return _json(202, {"id": snap_id})

    if path == "/api/reports/compare" and method == "GET":
        res = reports.compare(_reports_root(ctx), _q(query, "a"), _q(query, "b"))
        if res is None:
            return _json(404, {"error": "unknown snapshot"})
        return _json(200, res)

    if path == "/api/reports/diff" and method == "GET":
        root = _reports_root(ctx)
        a, b, style = _q(query, "a"), _q(query, "b"), _q(query, "style", "side")
        if style not in ("side", "unified"):
            return _json(400, {"error": "style must be side or unified"})
        if reports.read_manifest(root, a) is None or reports.read_manifest(root, b) is None:
            return _json(404, {"error": "unknown snapshot"})
        res = reports.diff(root, a, b, _q(query, "report"), style)
        if res is None:
            return _json(404, {"error": "unknown report"})
        return _json(200, res)

    if path == "/api/reports/raw" and method == "GET":
        text = reports.report_text(_reports_root(ctx), _q(query, "id"), _q(query, "report"))
        if text is None:
            return _json(404, {"error": "unknown snapshot or report"})
        return 200, [("Content-Type", "text/plain; charset=utf-8")], text.encode("utf-8")

    if path == "/api/launch" and method == "POST":
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            return _json(400, {"error": "bad json"})
        mode = payload.get("mode")
        if mode not in ("stage", "upgrade"):
            return _json(400, {"error": "mode must be stage or upgrade"})
        if _snapshot_busy(ctx):
            return _json(409, {"error": "report snapshot in progress"})
        overrides = payload.get("overrides", [])
        if not isinstance(overrides, list):
            return _json(400, {"error": "overrides must be a list of check names"})
        results = checks.run_checks(ctx.host, _load_checks(ctx), "preflight", mode, _machtype(ctx))
        blocking = checks.blocking_failures(results)
        # Every blocking failure must be overridden by name; names for checks
        # that now pass (or never existed) are simply ignored.
        missing = [b["name"] for b in blocking if b["name"] not in overrides]
        if missing:
            return _json(409, {"error": "pre-flight checks failed", "blocking": blocking,
                               "not_overridden": missing})
        if blocking:
            reason = payload.get("reason") or ""
            reason = reason.strip() if isinstance(reason, str) else ""
            if not reason:
                return _json(400, {"error": "override requires a reason"})
            ctx.audit.record("override", mode=mode, client=client_ip, reason=reason,
                             checks=[b["name"] for b in blocking])
        ctx.audit.record("launch", mode=mode, client=client_ip)
        # The httpd layer performs the actual launch (side effect); the
        # dispatcher records intent and returns accepted. See httpd on_launch.
        return _json(202, {"accepted": True, "mode": mode})

    return _json(404, {"error": "not found"})

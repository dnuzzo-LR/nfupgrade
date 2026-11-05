"""api.py — pure request dispatcher. Holds routing, auth gating, and JSON
shaping so the HTTP layer (httpd.py) stays a thin adapter and the API is
unit-testable without sockets."""
import collections
import json
import signal
import threading
import time

import os

from nfupgrader import progress, logtail, launcher, localinfo, siteview, site, checks, cfg
from nfupgrader import reports, snapjobs, installstate, steps, cnccnfg, bootmenu
from nfupgrader import patches
from nfupgrader import preview as previewdata
from nfupgrader import license as licmod

Context = collections.namedtuple("Context", [
    "host", "hostname", "inclogdir", "phase", "tracefile",
    "auth", "audit", "webdir", "nf_upgrade_path",
    "effective_config", "user_config", "launch_cwd", "cnc_cnfg_path",
    "peer_host_factory", "checks_path", "state", "depot_dirs",
    "reports_path", "reports_root", "runner",
    "nf_install_path", "hosts_path", "license_path", "nf_patch_path",
])
# The trailing fields are only used by side effects (launch), info gathering,
# checks, the config builder, or reports; default them so the dispatcher/tests
# need not supply each one. (namedtuple field defaults via __new__.__defaults__ — py3.6.)
Context.__new__.__defaults__ = (None,) * 15

# One check fix at a time: fixes change system state as root (dnf, sysctl, limits).
# Held for the whole fix request, so launches and snapshots are refused meanwhile
# (and httpd holds it while spawning a launch, so a fix cannot start then). A
# boot menu start holds it too, and fixes/launches check the boot menu under it.
_FIX_LOCK = threading.Lock()

# Seconds after an accepted launch during which /api/checks/fix is refused, even
# if launcher.upgrader_running() has not yet caught up (there is a short window
# between an accepted launch and nf_upgrader actually showing up in pgrep).
FIX_LAUNCH_GRACE = 120

# Injectable clock: tests patch api._now to move time without sleeping.
_now = time.time


def fix_in_progress():
    """True while a check fix (or a launch spawn) holds _FIX_LOCK."""
    return _FIX_LOCK.locked()


def _json_object(body):
    """The request body parsed as a JSON object, or None if it is not one."""
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except ValueError:                       # includes UnicodeDecodeError
        return None
    return payload if isinstance(payload, dict) else None


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


def active_config(ctx):
    """The upgrade config Launch will use: one saved on the Config tab, else
    NFU_USER_CONFIG (None if neither)."""
    st = ctx.state if isinstance(ctx.state, dict) else {}
    return st.get("user_config") or ctx.user_config


def upgrade_logdir(ctx):
    """Where nf_upgrade/nf_upgrader write the state file and traces. They take
    INCLOGDIR from the upgrade config, so read it from the active config; fall
    back to nfupgrader's own INCLOGDIR when there is no config, it doesn't set
    one, or the value isn't a plain absolute path ($VAR needs the shell)."""
    path = active_config(ctx)
    if path:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                d = cfg.parse_cfg(fh.read()).get("INCLOGDIR", "").strip()
        except (IOError, OSError):
            d = ""
        if _plain_abs_path(d):
            return d
    return ctx.inclogdir


def _plain_abs_path(value):
    """True for an absolute path the shell would not expand ($VAR, `cmd`)."""
    return value.startswith("/") and "$" not in value and "`" not in value


def install_config_error(ctx):
    """Why an install/resume launch cannot use the active config, or None.
    nf_install sources that config: it needs CNCCNFG and DEPOTFILE readable
    files, and INCLOGDIR (its state/info/trace dir) an existing writable
    absolute directory outside /usr/cnc, which a reset removes (the same plain
    absolute path rule as upgrade_logdir)."""
    path = active_config(ctx)
    if not path:
        return "save an install configuration on the Config tab first"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            values = cfg.parse_cfg(fh.read())
    except (IOError, OSError):
        return "cannot read the install configuration {}".format(path)
    for key in ("DEPOTFILE", "CNCCNFG"):
        v = values.get(key, "").strip()
        if not v:
            return "{} is not set in {}".format(key, path)
        if not (os.path.isfile(v) and os.access(v, os.R_OK)):
            return "{} {} is not a readable file".format(key, v)
    d = values.get("INCLOGDIR", "").strip()
    if not d:
        return "INCLOGDIR is not set in {}".format(path)
    if not _plain_abs_path(d):
        return "INCLOGDIR {} must be a plain absolute path".format(d)
    norm = os.path.normpath(d)
    if norm == "/usr/cnc" or norm.startswith("/usr/cnc/"):
        return "INCLOGDIR {} must not be under /usr/cnc".format(d)
    if not (os.path.isdir(d) and os.access(d, os.W_OK)):
        return "INCLOGDIR {} is not an existing writable directory".format(d)
    return None


PREVIEW_REFUSED = "preview mode \u2014 nothing will be installed or changed"


def preview_scenario(ctx):
    """The NFU_PREVIEW_INSTALL scenario (see preview.py), or None when preview
    is off."""
    st = ctx.state if isinstance(ctx.state, dict) else {}
    return st.get("preview")


def host_mode(ctx):
    """'install' while this host is mid-install or not yet installed (or in
    preview), else 'upgrade'. See installstate.host_mode."""
    if preview_scenario(ctx):
        return "install"
    return installstate.host_mode(ctx.host, ctx.hostname, upgrade_logdir(ctx))


def _bootmenu_session(ctx):
    """The BootMenuSession object in ctx.state["bootmenu"], created by httpd
    (Task 5) when the tab is first opened; None if there is none (treated as
    state "none" -- not running -- per the controller's ruling)."""
    st = ctx.state if isinstance(ctx.state, dict) else {}
    return st.get("bootmenu")


#: session() shape reported when there is no BootMenuSession object at all.
_BOOTMENU_NO_SESSION = {"state": "none", "pid": None, "started_at": None, "exit_code": None}


def bootmenu_availability(ctx):
    """Whether the Boot menu tab can be opened right now: {"available": bool,
    "greyed": bool, "reason": str, "other": "none"|"foreign"|"leftover",
    "other_pids": [int]}. Checked in order: preview mode; not installed (or an
    unfinished install); an install/upgrade running; our own session already
    running (reattach); some other new_boot_nms/boot_nms on the box, which is
    either a leftover from an earlier run of this tool (pidfile says so, and
    its tool process is dead) or someone else's (a live console login, etc).

    greyed (controller ruling R5) is True only for "netFLEX is not installed"
    and "preview mode" -- the tab itself is hidden/disabled for those. For
    every other unavailable reason (an install/upgrade running, foreign,
    leftover) the tab opens and shows the reason (and, for leftover, an End
    it button) in place of the terminal, so greyed is False even though
    available is False."""
    no_other = {"other": "none", "other_pids": []}
    if preview_scenario(ctx):
        return dict(no_other, available=False, greyed=True, reason="preview mode")
    if host_mode(ctx) == "install":
        return dict(no_other, available=False, greyed=True,
                    reason="netFLEX is not installed")
    if launcher.anything_running(ctx.host):
        return dict(no_other, available=False, greyed=False,
                    reason="an install, upgrade or patch is running")
    session = _bootmenu_session(ctx)
    if session is not None and session.running():
        return dict(no_other, available=True, greyed=False, reason="")
    own_pids = session.own_pids() if session is not None else set()
    pidfile_path = os.path.join(ctx.inclogdir, bootmenu.PIDFILE_NAME)
    kind, pids = bootmenu.classify_others(bootmenu.list_processes(), own_pids, pidfile_path)
    if kind == "foreign":
        return {"available": False, "greyed": False,
                "reason": "someone else is running the boot menu",
                "other": "foreign", "other_pids": pids}
    if kind == "leftover":
        return {"available": False, "greyed": False,
                "reason": "a boot menu from an earlier run is still on this box",
                "other": "leftover", "other_pids": pids}
    return dict(no_other, available=True, greyed=False, reason="")


def _active_phase(ctx):
    """Which phase the progress strip and log follow: the mode of the last
    accepted launch, else install (if the host is mid-install or not yet
    installed), else NFU_PHASE, else inferred from the state file."""
    st = ctx.state if isinstance(ctx.state, dict) else {}
    if st.get("preview"):
        return "install"
    if st.get("phase") in ("stage", "upgrade", "install"):
        return st["phase"]
    if host_mode(ctx) == "install":
        return "install"
    if ctx.phase in ("stage", "upgrade"):
        return ctx.phase
    return progress.infer_phase(progress.read_state(ctx.hostname, upgrade_logdir(ctx)))


def _active_tracefile(ctx, phase):
    """An explicit NFU_TRACEFILE, else the newest trace for the phase: for
    install, the newest of either an install or a reset trace (a reset can run
    after a failed/unfinished install, and the Log tab should follow whichever
    ran most recently)."""
    if ctx.tracefile:
        return ctx.tracefile
    logdir = upgrade_logdir(ctx)
    if isinstance(ctx.state, dict) and ctx.state.get("log_follow") == "patch":
        return logtail.latest_trace(logdir, "patch", ctx.hostname)
    if phase == "install":
        return logtail.latest_of(logdir, ["install", "reset"], ctx.hostname)
    return logtail.latest_trace(logdir, phase, ctx.hostname)


def _reports_root(ctx):
    return ctx.reports_root or os.path.join(ctx.inclogdir, "nfupgrader_reports")


def _snapshot_busy(ctx):
    return ctx.runner is not None and ctx.runner.busy()


def _interrupted_notice(ctx):
    """A notice when the newest snapshot is a before that was interrupted (e.g.
    the service restarted mid-snapshot): its `then` never ran, so nf_upgrade
    was never launched. None otherwise. Call only when no job is running."""
    snaps = reports.list_snapshots(_reports_root(ctx), None)
    if not snaps:
        return None
    newest = snaps[0]
    if newest["label"] != "before" or newest["status"] != "incomplete":
        return None
    if any(s.get("pair_of") == newest["id"] for s in snaps if s["label"] == "after"):
        return None
    if launcher.anything_running(ctx.host):
        return None
    return "Before snapshot {} was interrupted \u2014 the upgrade was not launched.".format(
        newest["id"])


def _license_path(ctx):
    return ctx.license_path or licmod.LIC_PATH


def license_editable(ctx, holding_lock=False):
    """(bool, reason): whether the license may be changed right now.
    holding_lock: the caller holds _FIX_LOCK, so skip the lock test."""
    if preview_scenario(ctx):
        return False, "preview mode"
    if ctx.host.run(["test", "-f", licmod.APP_UP]).rc == 0:
        return False, "netFLEX is running \u2014 stop it to edit the license"
    if launcher.anything_running(ctx.host):
        return False, "an install, upgrade or patch is running"
    session = _bootmenu_session(ctx)
    if session is not None and session.running():
        return False, "the boot menu is open"
    state = ctx.state if isinstance(ctx.state, dict) else {}
    launched_at = state.get("launched_at")
    if launched_at is not None and _now() - launched_at < FIX_LAUNCH_GRACE:
        return False, "an upgrade or install was just launched"
    if not holding_lock and fix_in_progress():
        return False, "another change is in progress"
    if _snapshot_busy(ctx):
        return False, "a report snapshot is running"
    return True, ""


def _license_body(ctx, holding_lock=False):
    """The GET /api/license body."""
    res = licmod.read_license(ctx.host, _license_path(ctx))
    ok, reason = license_editable(ctx, holding_lock)
    res["editable"], res["reason"], res["fields"] = ok, reason, licmod.FIELDS
    if res["state"] == "ok":
        res["expiry"] = licmod.expiry_of(res["license"])
    return res


def _license_request(payload):
    """(password, values, expiry, ne_on, errors) from a review/save body."""
    password = payload.get("password")
    values = payload.get("values")
    expiry = payload.get("expiry") or ""
    ne_on = payload.get("ne_on")
    if not isinstance(password, str):
        return None, None, None, None, [{"field": "password", "message": "required"}]
    if not isinstance(values, dict):
        return None, None, None, None, [{"field": "values", "message": "must be an object"}]
    if not isinstance(expiry, str):
        return None, None, None, None, [{"field": "expiry", "message": "must be text"}]
    if ne_on is not None and (not isinstance(ne_on, list) or not all(
            isinstance(n, int) and not isinstance(n, bool) for n in ne_on)):
        return None, None, None, None, [{"field": "ne_on", "message": "must be a list of numbers"}]
    return password, values, expiry, ne_on, licmod.validate(values, expiry, ne_on)


def _license_repair(payload, client_ip, ctx, lp):
    """POST /api/license/repair {password, expiry}: set a real expiry on a
    license lice crashes on (state needs_repair). Same guards as save: the
    change lock, editability rechecked under it, password checked before the
    backup, backup before any write."""
    password = payload.get("password")
    expiry = payload.get("expiry")
    if not isinstance(password, str) or not isinstance(expiry, str):
        return _json(400, {"errors": [{"field": "password/expiry", "message": "required"}]})
    if licmod._norm_expiry(expiry) is None:
        return _json(400, {"errors": [{"field": "expiry",
                                       "message": "use MM/DD/YYYY (year 1970 or later)"}]})
    if not _FIX_LOCK.acquire(False):
        return _json(409, {"error": "another change is in progress"})
    try:
        ok, reason = license_editable(ctx, holding_lock=True)
        if not ok:
            return _json(409, {"error": reason})
        if licmod.read_license(ctx.host, lp)["state"] != "needs_repair":
            return _json(409, {"error": "license does not need repair"})
        pw_err = licmod._pw_error(password)
        if pw_err:
            ctx.audit.record("license_repair_failed", client=client_ip, error=pw_err)
            return _json(400, {"ok": False, "error": pw_err})
        un = licmod.unlock(ctx.host, password, lp)
        if not un["ok"]:
            ctx.audit.record("license_repair_failed", client=client_ip, error=un["error"])
            return _json(403, {"ok": False, "error": un["error"]})
        try:
            bak = licmod.backup(lp)
        except Exception as e:
            return _json(500, {"ok": False, "error": "backup failed: {}".format(e)})
        res = licmod.repair(ctx.host, password, expiry, lp)
        if res["ok"]:
            ctx.audit.record("license_repaired", client=client_ip, backup=bak,
                             expiry=licmod._norm_expiry(expiry))
        else:
            ctx.audit.record("license_repair_failed", client=client_ip, backup=bak,
                             error=res["error"])
        out = dict(res)
        out["backup"] = bak
        return _json(200, out)
    finally:
        _FIX_LOCK.release()


def _license_dispatch(method, path, body, client_ip, ctx):
    """Handle /api/license*; returns a _json response."""
    lp = _license_path(ctx)
    if path == "/api/license" and method == "GET":
        return _json(200, _license_body(ctx))
    if method != "POST":
        return _json(404, {"error": "not found"})
    if path in ("/api/license/create", "/api/license/migrate"):
        creating = path.endswith("create")
        need = "missing" if creating else "old_format"
        if not _FIX_LOCK.acquire(False):
            return _json(409, {"error": "another change is in progress"})
        try:
            ok, reason = license_editable(ctx, holding_lock=True)
            if not ok:
                return _json(409, {"error": reason})
            if licmod.read_license(ctx.host, lp)["state"] != need:
                return _json(409, {"error": "license state is not {}".format(need)})
            if not creating:
                res = licmod.migrate(ctx.host)
                ctx.audit.record("license_migrated", client=client_ip, rc=res["rc"])
                return _json(200, {"ok": res["ok"], "rc": res["rc"]})
            try:
                tpl = licmod.create_from_template(lp)
            except (ValueError, OSError) as e:
                return _json(500, {"ok": False, "error": str(e)})
            mig = None
            if licmod.read_license(ctx.host, lp)["state"] == "old_format":
                m = licmod.migrate(ctx.host)
                mig = {"ok": m["ok"], "rc": m["rc"]}
            extra = {"migrate": mig} if mig is not None else {}
            ctx.audit.record("license_created", client=client_ip, template=tpl, **extra)
            body_out = _license_body(ctx, True)
            body_out.update(extra)
            return _json(200, body_out)
        finally:
            _FIX_LOCK.release()

    if path not in ("/api/license/unlock", "/api/license/review", "/api/license/save",
                    "/api/license/repair"):
        return _json(404, {"error": "not found"})
    payload = _json_object(body)
    if payload is None:
        return _json(400, {"error": "bad json"})

    if path == "/api/license/repair":
        return _license_repair(payload, client_ip, ctx, lp)

    if path == "/api/license/unlock":
        if licmod.read_license(ctx.host, lp)["state"] != "ok":
            return _json(409, {"error": "license is not readable"})
        pw = payload.get("password")
        res = licmod.unlock(ctx.host, pw if isinstance(pw, str) else "", lp)
        if not res["ok"]:
            ctx.audit.record("license_unlock_failed", client=client_ip)
        return _json(200 if res["ok"] else 403, res)

    password, values, expiry, ne_on, errors = _license_request(payload)
    if errors:
        return _json(400, {"errors": errors})

    if path == "/api/license/review":
        cur = licmod.read_license(ctx.host, lp)
        if cur["state"] != "ok":
            return _json(409, {"error": "license is not readable"})
        un = licmod.unlock(ctx.host, password, lp)
        if not un["ok"]:
            ctx.audit.record("license_unlock_failed", client=client_ip)
            return _json(403, {"error": un["error"]})
        return _json(200, licmod.diff(cur["license"], values, expiry, ne_on,
                                      un["ne_types"]))

    # save
    if not _FIX_LOCK.acquire(False):
        return _json(409, {"error": "another change is in progress"})
    try:
        ok, reason = license_editable(ctx, holding_lock=True)
        if not ok:
            return _json(409, {"error": reason})
        cur = licmod.read_license(ctx.host, lp)
        if cur["state"] != "ok":
            return _json(409, {"error": "license is not readable"})
        # Check the password before backup(): each backup rotates an older one
        # out, so a run of typos must not push out the pre-change copy.
        pw_err = licmod._pw_error(password)
        if pw_err:
            ctx.audit.record("license_save_failed", client=client_ip, error=pw_err)
            return _json(400, {"ok": False, "error": pw_err})
        un = licmod.unlock(ctx.host, password, lp)
        if not un["ok"]:
            ctx.audit.record("license_save_failed", client=client_ip,
                             error=un["error"])
            return _json(403, {"ok": False, "error": un["error"]})
        # Same NE table as review, so save toggles only types the user saw.
        d = licmod.diff(cur["license"], values, expiry, ne_on, un["ne_types"])
        if not (d["changes"] or d["ne_toggle"] or d["expiry"]):
            return _json(200, {"ok": True, "applied": []})
        try:
            bak = licmod.backup(lp)
        except Exception as e:
            return _json(500, {"ok": False, "error": "backup failed: {}".format(e)})
        res = licmod.apply(ctx.host, password, d, lp)
        if res["ok"]:
            ctx.audit.record("license_saved", client=client_ip, backup=bak,
                             changes=d["changes"], ne_toggle=d["ne_toggle"],
                             expiry=d["expiry"], applied=res["applied"],
                             not_applied=res["not_applied"])
        else:
            ctx.audit.record("license_save_failed", client=client_ip, backup=bak,
                             error=res["error"], not_applied=res["not_applied"])
        out = dict(res)
        out["backup"] = bak
        return _json(200, out)
    finally:
        _FIX_LOCK.release()


def _patch_job_completion(ctx):
    """If a patch job spawned by this service has exited, audit its outcome once
    and forget it. Never puts the Popen handle anywhere but ctx.state."""
    st = ctx.state if isinstance(ctx.state, dict) else {}
    job = st.get("patch_job")
    if not job:
        return
    rc = job["popen"].poll()
    if rc is None:
        return
    del st["patch_job"]
    if rc == 0:
        ctx.audit.record("patch_install_done", path=job.get("path"),
                         version=job.get("version"), client=job.get("client"))
    else:
        ctx.audit.record("patch_install_failed", rc=rc,
                         trace=logtail.latest_trace(upgrade_logdir(ctx), "patch", ctx.hostname),
                         path=job.get("path"), version=job.get("version"),
                         client=job.get("client"))


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

    if path == "/api/progress" and method == "GET" and preview_scenario(ctx):
        rec = previewdata.progress_record(preview_scenario(ctx))
        rec["logdir"] = upgrade_logdir(ctx)
        return _json(200, rec)

    if path == "/api/log" and method == "GET" and preview_scenario(ctx):
        # The canned trace is delivered whole on the first read (offset 0).
        offset = int((query.get("offset") or ["0"])[0])
        name = "preview-" + preview_scenario(ctx)
        seen = _q(query, "file")
        reset = bool(seen) and seen != name
        entries = previewdata.log_entries(preview_scenario(ctx), _q(query, "min") or None)
        return _json(200, {"entries": entries if (reset or not offset) else [],
                           "next_offset": 1, "file": name, "path": previewdata.TRACE,
                           "exists": True, "reset": reset})

    if path == "/api/install/status" and method == "GET" and preview_scenario(ctx):
        return _json(200, previewdata.status(preview_scenario(ctx), ctx.hostname))

    if path == "/api/preview" and method == "POST":
        if not preview_scenario(ctx):
            return _json(404, {"error": "not found"})
        payload = _json_object(body)
        scenario = (payload or {}).get("scenario")
        if scenario not in previewdata.SCENARIOS:
            return _json(400, {"error": "scenario must be one of {}".format(
                "/".join(previewdata.SCENARIOS))})
        ctx.state["preview"] = scenario
        return _json(200, {"preview": scenario})

    if method == "POST" and preview_scenario(ctx) and path in (
            "/api/launch", "/api/install/reset", "/api/checks/fix",
            "/api/license/save", "/api/license/create", "/api/license/migrate",
            "/api/license/repair"):
        return _json(409, {"error": PREVIEW_REFUSED})

    if path == "/api/license" or path.startswith("/api/license/"):
        return _license_dispatch(method, path, body, client_ip, ctx)

    if path == "/api/progress" and method == "GET":
        # Either engine can be live (nf_upgrader for stage/upgrade, nf_install
        # for a new install); at most one runs at a time.
        # nf_patch is deliberately excluded: a patch run is not upgrade/install progress.
        running = launcher.upgrader_running(ctx.host) or launcher.installer_running(ctx.host)
        logdir = upgrade_logdir(ctx)
        rec = progress.read_progress(ctx.hostname, _active_phase(ctx), logdir, running)
        rec["logdir"] = logdir
        return _json(200, rec)

    if path == "/api/log/live" and method == "GET":
        # The step in progress (see logtail.live_step); only while an engine runs,
        # since a buffer left by a killed run would otherwise look live forever.
        if preview_scenario(ctx):
            lines = previewdata.live_lines(preview_scenario(ctx))
            return _json(200, {"running": bool(lines),
                               "file": previewdata.TRACE if lines else None, "lines": lines})
        if not (launcher.upgrader_running(ctx.host) or launcher.installer_running(ctx.host)):
            return _json(200, {"running": False, "file": None, "lines": []})
        live, lines = logtail.live_step(upgrade_logdir(ctx))
        return _json(200, {"running": True, "file": live, "lines": lines})

    if path == "/api/log" and method == "GET":
        offset = int((query.get("offset") or ["0"])[0])
        min_sev = (query.get("min") or [None])[0]
        path = _active_tracefile(ctx, _active_phase(ctx))
        name = os.path.basename(path)
        # The client echoes the file it has been reading; a different file (a new
        # run, or the phase switched) restarts it from the top.
        seen = _q(query, "file")
        reset = bool(seen) and seen != name
        chunk = logtail.tail_since(path, 0 if reset else offset, min_severity=min_sev)
        chunk["file"] = name
        chunk["path"] = path
        chunk["exists"] = os.path.exists(path)
        chunk["reset"] = reset
        return _json(200, chunk)

    if path == "/api/info" and method == "GET":
        info = localinfo.gather(ctx.host, ctx.hostname,
                                ctx.cnc_cnfg_path or "/usr/cnc/features/cnc.cnfg")
        info["upgrade_logdir"] = upgrade_logdir(ctx)
        info["nfupgrader_logdir"] = ctx.inclogdir
        return _json(200, info)

    if path == "/api/site" and method == "GET":
        cnfg = ctx.cnc_cnfg_path or "/usr/cnc/features/cnc.cnfg"
        hosts = site.read_site(cnfg)
        factory = ctx.peer_host_factory or _default_peer_factory
        view = siteview.gather_site(hosts, ctx.hostname, cnfg, ctx.host, factory)
        return _json(200, view)

    if path == "/api/install/status" and method == "GET":
        logdir = upgrade_logdir(ctx)
        state = progress.read_state(ctx.hostname, logdir, "install")
        trace = logtail.latest_of(logdir, ["install", "reset"], ctx.hostname)
        fatal = [e["msg"] for e in logtail.tail(trace, "ERROR", 10)]
        installed = installstate.netflex_installed(ctx.host)
        return _json(200, {
            "mode": installstate.host_mode(ctx.host, ctx.hostname, logdir, installed=installed),
            "installed": installed,
            "running": launcher.installer_running(ctx.host),
            "unfinished": installstate.unfinished(ctx.hostname, logdir),
            "state": state,
            "label": steps.label(state) if state else "",
            "info": installstate.read_install_info(ctx.hostname, logdir),
            "trace": trace,
            "fatal": fatal,
        })

    if path == "/api/install/reset" and method == "POST":
        payload = _json_object(body)
        if payload is None:
            return _json(400, {"error": "bad json"})
        session = _bootmenu_session(ctx)
        if session is not None and session.running():
            return _json(409, {"error": "the boot menu is open"})
        logdir = upgrade_logdir(ctx)
        if not installstate.unfinished(ctx.hostname, logdir):
            return _json(409, {"error": "no unfinished install to reset"})
        if launcher.anything_running(ctx.host):
            return _json(409, {"error": "install in progress"})
        if fix_in_progress():
            return _json(409, {"error": "check fix in progress"})
        if _snapshot_busy(ctx):
            return _json(409, {"error": "report snapshot in progress"})
        reason = payload.get("reason") or ""
        reason = reason.strip() if isinstance(reason, str) else ""
        if not reason:
            return _json(400, {"error": "reset requires a reason"})
        ctx.audit.record("install_reset", reason=reason, client=client_ip)
        # The httpd layer performs the actual reset (side effect); the
        # dispatcher records intent and returns accepted.
        return _json(202, {"accepted": True, "action": "reset"})

    if path == "/api/checks/fix" and method == "POST":
        payload = _json_object(body)
        if payload is None:
            return _json(400, {"error": "bad json"})
        # Take the lock before looking at system state, so no launch, snapshot
        # or boot menu start can slip in between these checks and the fix itself.
        if not _FIX_LOCK.acquire(False):
            return _json(409, {"error": "another fix is running"})
        try:
            session = _bootmenu_session(ctx)
            if session is not None and session.running():
                return _json(409, {"error": "the boot menu is open"})
            state = ctx.state if isinstance(ctx.state, dict) else {}
            launched_at = state.get("launched_at")
            if launched_at is not None and _now() - launched_at < FIX_LAUNCH_GRACE:
                return _json(409, {"error": "upgrade just launched"})
            if launcher.anything_running(ctx.host):
                return _json(409, {"error": "upgrade in progress"})
            if _snapshot_busy(ctx):
                return _json(409, {"error": "report snapshot in progress"})
            phase = payload.get("phase") or _active_phase(ctx)
            if phase not in ("stage", "upgrade", "install"):
                return _json(400, {"error": "phase must be stage, upgrade, or install"})
            name = payload.get("name") or ""
            check = checks.find_check(_load_checks(ctx), name, "preflight", phase,
                                      _machtype(ctx))
            if check is None:
                return _json(404, {"error": "unknown check"})
            what = checks.describe_fix(check)
            if what is None:
                return _json(400, {"error": "no fix for this check"})
            before = checks.evaluate(ctx.host, check, "preflight")
            if before["passed"]:
                return _json(200, {"fix": None, "result": before})
            ctx.audit.record("check_fix_start", name=name, kind=check["kind"], fix=what,
                             client=client_ip)
            ok, detail = checks.apply_fix(ctx.host, check)
            ctx.audit.record("check_fix", name=name, kind=check["kind"], ok=ok, detail=detail,
                             client=client_ip)
            # A completed attempt (success or failure) for this check resolves
            # any startup-detected interrupted fix of the same name.
            if isinstance(ctx.state, dict) and (ctx.state.get("interrupted_fix") or {}).get("name") == name:
                ctx.state["interrupted_fix"] = None
            after = checks.evaluate(ctx.host, check, "preflight")
            return _json(200, {"fix": {"ok": ok, "detail": detail}, "result": after})
        finally:
            _FIX_LOCK.release()

    if path == "/api/checks" and method == "GET":
        when = (query.get("when") or ["preflight"])[0]
        phase = (query.get("phase") or [_active_phase(ctx)])[0]
        if when == "postflight" and phase == "install":
            # Re-checks the rpm_installed preconditions a fix may have just
            # installed; nothing else from the install check list is relevant
            # post-install (no "upgrade just finished" style re-checks exist yet).
            results = checks.install_postflight(ctx.host, _load_checks(ctx))
        else:
            results = checks.run_checks(ctx.host, _load_checks(ctx), when, phase, _machtype(ctx))
        return _json(200, {"results": results, "blocking": checks.blocking_failures(results)})

    if path == "/api/config/fields" and method == "GET":
        mode = _q(query, "mode") or host_mode(ctx)
        try:
            raw_fields = cfg.fields_for(mode)
        except ValueError:
            return _json(400, {"error": "mode must be one of {}".format("/".join(cfg.MODES))})
        # The upgrade logs where nfupgrader keeps its own files by default: a
        # fixed place outside the netFLEX load (whose /usr/cnc/logs moves when the
        # upgrade switches /usr/cnc) and where the progress/log views look.
        fields = [dict(f, default=ctx.inclogdir) if f["name"] == "INCLOGDIR" and ctx.inclogdir
                  else f for f in raw_fields]
        return _json(200, {"fields": fields, "mode": mode, "hostname": ctx.hostname})

    if path == "/api/install/hosts" and method == "GET":
        return _json(200, {"hosts": cnccnfg.read_etc_hosts(ctx.hosts_path or "/etc/hosts")})

    if path == "/api/config/depots" and method == "GET":
        dirs = ctx.depot_dirs or ["/opt/apt/upgrade", "."]
        return _json(200, {"depots": cfg.list_depots(ctx.host, dirs), "dirs": dirs})

    if path == "/api/config/preview" and method == "POST":
        payload = _json_object(body)
        if payload is None:
            return _json(400, {"error": "bad json"})
        values = payload.get("values", {})
        mode = payload.get("mode") or "upgrade"
        try:
            issues = cfg.validate(values, mode, ctx.hostname)
        except ValueError:
            return _json(400, {"error": "mode must be one of {}".format("/".join(cfg.MODES))})
        if mode == "install":
            boxtype = str(values.get("BOXTYPE") or "").strip()
            mate = str(values.get("MATEHOST") or "").strip()
            if boxtype in ("FEP", "BEP") and mate:
                hosts = cnccnfg.read_etc_hosts(ctx.hosts_path or "/etc/hosts")
                names = set(h["name"] for h in hosts)
                if mate not in names:
                    issues.append({"field": "MATEHOST", "level": "WARNING",
                                   "message": "{} is not in /etc/hosts".format(mate)})
            try:
                cnc_cnfg = cnccnfg.render_from_values(values, ctx.hostname)
            except ValueError:
                cnc_cnfg = None
        else:
            cnc_cnfg = None
        return _json(200, {"issues": issues, "rendered": cfg.render_cfg(values),
                           "cnc_cnfg": cnc_cnfg})

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
        payload = _json_object(body)
        if payload is None:
            return _json(400, {"error": "bad json"})
        values = payload.get("values", {})
        mode = payload.get("mode") or "upgrade"
        try:
            issues = cfg.validate(values, mode, ctx.hostname)
        except ValueError:
            return _json(400, {"error": "mode must be one of {}".format("/".join(cfg.MODES))})
        if any(i["level"] == "ERROR" for i in issues):
            return _json(400, {"error": "config invalid", "issues": issues})
        cnc_path = None
        cnc_text = None
        if mode == "install":
            cnc_path = os.path.join(ctx.inclogdir, "nfupgrader_preview_cnc.cnfg"
                                    if preview_scenario(ctx) else "nfupgrader_cnc.cnfg")
            # Render into a local first: cfg.validate does not check thishost
            # (ctx.hostname) itself, only MATEHOST/MASTERHOST, so an invalid
            # hostname is only caught here. Nothing is written to disk until
            # this and the .cfg render below both succeed, so a bad hostname
            # never truncates a previously-good cnc.cnfg.
            try:
                cnc_text = cnccnfg.render_from_values(values, ctx.hostname)
            except ValueError as exc:
                return _json(400, {"error": "config invalid",
                                   "issues": issues + [{"field": "CNCCNFG", "level": "ERROR",
                                                        "message": str(exc)}]})
            values = dict(values)
            values["CNCCNFG"] = cnc_path
        rendered = cfg.render_cfg(values)
        if mode == "install":
            with open(cnc_path, "w") as fh:
                fh.write(cnc_text)
        # Preview saves beside, never over, the real saved config, and never
        # becomes the config Launch would use.
        saved = os.path.join(ctx.inclogdir, "nfupgrader_preview.cfg"
                             if preview_scenario(ctx) else "nfupgrader_user.cfg")
        with open(saved, "w") as fh:
            fh.write(rendered)
        if ctx.state is not None and not preview_scenario(ctx):
            ctx.state["user_config"] = saved
        ctx.audit.record("config_saved", path=saved, mode=mode, client=client_ip)
        return _json(200, {"saved": saved})

    if path == "/api/reports" and method == "GET":
        live = ctx.runner.live_id() if ctx.runner is not None else None
        return _json(200, {"snapshots": reports.list_snapshots(_reports_root(ctx), live)})

    if path == "/api/reports/job" and method == "GET":
        job = ctx.runner.job() if ctx.runner is not None else None
        return _json(200, {"job": job,
                           "launch_error": (ctx.state or {}).get("launch_error"),
                           "notice": None if job else _interrupted_notice(ctx),
                           "interrupted_fix": (ctx.state or {}).get("interrupted_fix")})

    if path == "/api/reports/run" and method == "POST":
        if ctx.runner is None:
            return _json(503, {"error": "reports unavailable"})
        # A manual snapshot mid-upgrade (or mid-install) would capture a
        # half-upgraded system and could hold the runner when the watcher
        # wants the after snapshot.
        if launcher.anything_running(ctx.host):
            return _json(409, {"error": "upgrade in progress"})
        # Held only while the job is registered: a fix that arrives afterwards
        # sees the runner busy, and one already running makes this a 409.
        if not _FIX_LOCK.acquire(False):
            return _json(409, {"error": "check fix in progress"})
        try:
            snap_id = ctx.runner.start("manual", client=client_ip)
        except snapjobs.Busy:
            return _json(409, {"error": "report snapshot in progress"})
        finally:
            _FIX_LOCK.release()
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
        headers = [("Content-Type", "text/plain; charset=utf-8"),
                  ("X-Content-Type-Options", "nosniff")]
        return 200, headers, text.encode("utf-8")

    if path == "/api/bootmenu/status" and method == "GET":
        result = bootmenu_availability(ctx)
        session = _bootmenu_session(ctx)
        result["session"] = session.state() if session is not None else dict(_BOOTMENU_NO_SESSION)
        result["busy"] = session.busy() if session is not None else False
        return _json(200, result)

    if path == "/api/bootmenu/end" and method == "POST":
        session = _bootmenu_session(ctx)
        if session is None or not session.running():
            return _json(409, {"error": "no boot menu session is running"})
        # Audited (once, forced=True) by the session's on_exit hook.
        session.end()
        return _json(200, {"ended": True})

    if path == "/api/bootmenu/end-leftover" and method == "POST":
        if bootmenu_availability(ctx)["other"] != "leftover":
            return _json(409, {"error": "no leftover boot menu to end"})
        # Re-read /proc and re-classify right before killing anything: the
        # availability check above may be stale (processes come and go), and
        # only pids classify_others still calls "leftover" -- the recorded
        # session pid and its descendants, with the tool pid still dead --
        # are ever targeted.
        session = _bootmenu_session(ctx)
        own_pids = session.own_pids() if session is not None else set()
        pidfile_path = os.path.join(ctx.inclogdir, bootmenu.PIDFILE_NAME)
        kind, pids = bootmenu.classify_others(bootmenu.list_processes(), own_pids, pidfile_path)
        if kind != "leftover":
            return _json(409, {"error": "no leftover boot menu to end"})
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            os.unlink(pidfile_path)
        except OSError:
            pass
        ctx.audit.record("bootmenu_end_leftover", pids=pids)
        return _json(200, {"ended": True})

    if path == "/api/launch" and method == "POST":
        payload = _json_object(body)
        if payload is None:
            return _json(400, {"error": "bad json"})
        session = _bootmenu_session(ctx)
        if session is not None and session.running():
            return _json(409, {"error": "the boot menu is open"})
        mode = payload.get("mode")
        if mode not in ("stage", "upgrade", "install"):
            return _json(400, {"error": "mode must be stage, upgrade, or install"})
        if _snapshot_busy(ctx):
            return _json(409, {"error": "report snapshot in progress"})
        if fix_in_progress():
            return _json(409, {"error": "check fix in progress"})
        if launcher.anything_running(ctx.host):
            return _json(409, {"error": "upgrade already running"})
        if mode == "install":
            # Fresh install and resume alike: nf_install sources this config.
            # Checked before anything reads the logdir it names.
            err = install_config_error(ctx)
            if err:
                return _json(409, {"error": err})
        hm = host_mode(ctx)
        if mode == "install":
            if hm != "install":
                return _json(409, {"error": "netFLEX is already installed"})
        elif hm != "upgrade":
            return _json(409, {"error": "netFLEX is not installed"})
        overrides = payload.get("overrides", [])
        if not isinstance(overrides, list):
            return _json(400, {"error": "overrides must be a list of check names"})
        action = None
        if mode == "install":
            action = ("resume" if installstate.unfinished(ctx.hostname, upgrade_logdir(ctx))
                      else "install")
        results = checks.run_checks(ctx.host, _load_checks(ctx), "preflight", mode, _machtype(ctx))
        if action == "resume":
            # Resuming skips the fresh-install-only hard preconditions (e.g.
            # "netFLEX not already installed" -- resuming an install that got
            # as far as the RPM step is expected to find it already there).
            results = [r for r in results if not r.get("hard")]
        hard = checks.hard_failures(results)
        if hard:
            # Hard failures are a stop, not a blocking check: never
            # overridable, regardless of what the caller passed.
            return _json(409, {"error": "fresh-install precondition failed", "hard": hard})
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
        ctx.audit.record("launch", mode=mode, action=action, client=client_ip)
        # The httpd layer performs the actual launch (side effect); the
        # dispatcher records intent and returns accepted. See httpd on_launch.
        return _json(202, {"accepted": True, "mode": mode, "action": action})

    if path == "/api/patches" and method == "GET":
        _patch_job_completion(ctx)
        unavailable = None
        if preview_scenario(ctx):
            unavailable = "preview mode"
        elif host_mode(ctx) == "install":
            unavailable = "netFLEX is not installed"
        out = patches.gather(ctx.host, ctx.depot_dirs)
        trace = logtail.latest_trace(upgrade_logdir(ctx), "patch", ctx.hostname)
        result = None
        try:
            with open(trace, encoding="utf-8", errors="replace") as fh:
                lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
            if lines and "RESULT:" in lines[-1]:
                result = lines[-1]
        except (IOError, OSError):
            trace = None
        if trace is not None and not os.path.exists(trace):
            trace = None
        out["running"] = launcher.patcher_running(ctx.host)
        out["available"] = unavailable is None
        out["reason"] = unavailable
        out["last"] = {"trace": trace, "result": result}
        return _json(200, out)

    if path == "/api/patches/install" and method == "POST":
        payload = _json_object(body)
        if payload is None:
            return _json(400, {"error": "bad json"})
        want = payload.get("path")
        if not isinstance(want, str) or not want:
            return _json(400, {"error": "path required"})
        if preview_scenario(ctx):
            return _json(409, {"error": "preview mode"})
        session = _bootmenu_session(ctx)
        if session is not None and session.running():
            return _json(409, {"error": "the boot menu is open"})
        if fix_in_progress():
            return _json(409, {"error": "check fix in progress"})
        if _snapshot_busy(ctx):
            return _json(409, {"error": "report snapshot in progress"})
        if launcher.anything_running(ctx.host):
            return _json(409, {"error": "an install, upgrade or patch is running"})
        # Only a path the depot scan itself produced is ever handed to nf_patch.
        rows = patches.gather(ctx.host, ctx.depot_dirs).get("patches", [])
        row = None
        for r in rows:
            if r.get("path") == want:
                row = r
                break
        if row is None or row.get("status") != "installable" or row.get("needs_stop"):
            reason = (row.get("reason") if row else "") or "not in the depot list"
            ctx.audit.record("patch_install_refused", path=want, reason=reason,
                             client=client_ip)
            return _json(400, {"error": reason})
        ctx.audit.record("patch_install_started", path=want, version=row.get("version"),
                         client=client_ip)
        # httpd (patch_side_effect) spawns nf_patch from this 202.
        return _json(202, {"accepted": True, "path": want, "version": row.get("version")})

    return _json(404, {"error": "not found"})

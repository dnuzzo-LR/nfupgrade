"""httpd.py — thin http.server adapter over api.dispatch, plus main() that binds
loopback and prints the one-time access URL. Serves static files from web/."""
import json
import os
import posixpath
import socketserver
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded server so one slow request (e.g. an unresponsive peer's ssh in
    /api/site) cannot block the rest of the UI. (Python 3.6-safe; stdlib adds
    http.server.ThreadingHTTPServer only in 3.7.)"""
    daemon_threads = True

from nfupgrader import api, launcher, progress, snapjobs
from nfupgrader.auth import TokenAuth
from nfupgrader.audit import AuditLog, last_unfinished_fix
from nfupgrader.host import Host, SshHost

_CTYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


def parse_cookies(header):
    """Parse a Cookie header into a dict."""
    out = {}
    for part in (header or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def client_ip_from(client_address):
    """Extract the client IP from a (host, port) tuple."""
    return client_address[0]


def resolve_url_host(bind, url_host_override, hostname):
    """Pick the host to print in the access URL. An explicit override wins; a
    wildcard/empty bind means the server is reachable by name, so use the
    hostname; otherwise the specific bind address is the reachable host."""
    if url_host_override:
        return url_host_override
    if bind in ("0.0.0.0", "::", ""):
        return hostname
    return bind


def resolve_launch_cwd(env_cwd, user_config):
    """Directory to run nf_upgrade from (for relative DEPOTFILE/linux_packages
    lookup): an explicit NFU_LAUNCH_CWD wins, else the user config's directory,
    else None (inherit the server's cwd)."""
    if env_cwd:
        return env_cwd
    if user_config:
        return os.path.dirname(user_config)
    return None


DEFAULT_DEPOT_DIR = "/opt/apt/upgrade"


def resolve_depot_dirs(env_dirs, start_dir):
    """Directories scanned for netFLEX-CORE RPMs. An explicit NFU_DEPOT_DIRS
    (colon-separated) wins; otherwise the directory run.sh was started from,
    then /opt/apt/upgrade. Missing directories are skipped by the scan."""
    if env_dirs:
        return [d for d in env_dirs.split(":") if d]
    dirs = [start_dir] if start_dir else []
    if DEFAULT_DEPOT_DIR not in dirs:
        dirs.append(DEFAULT_DEPOT_DIR)
    return dirs


def resolve_inclogdir(env_dir, start_dir):
    """nfupgrader's own directory (audit, saved/effective config, report
    snapshots): an explicit INCLOGDIR wins, else <dir run.sh started from>/logs."""
    return env_dir or os.path.join(start_dir, "logs")


def ensure_dir(path):
    """Create path (and parents) if missing; return it."""
    if not os.path.isdir(path):
        os.makedirs(path)
    return path


def perform_launch(ctx, mode):
    """Spawn nf_upgrade. The active config is one chosen in the UI (state), else
    NFU_USER_CONFIG; an effective config is generated from it (sources it + forces
    PROMPTFORVALIDATE=NO) — the customer's own file is never edited."""
    resolved = (ctx.state or {}).get("user_config") or ctx.user_config
    if resolved:
        launcher.write_effective_config(resolved, ctx.effective_config)
    launcher.launch(ctx.nf_upgrade_path, ctx.effective_config, mode, cwd=ctx.launch_cwd)


def load_interrupted_fix(audit, path):
    """Detect a check fix left unfinished by a service restart mid-fix (see
    audit.last_unfinished_fix), audit it once as "check_fix_interrupted", and
    return it (or None) for state["interrupted_fix"]. Called once at startup."""
    interrupted = last_unfinished_fix(path)
    if interrupted:
        audit.record("check_fix_interrupted", **interrupted)
    return interrupted


def upgrade_status(ctx):
    """The upgrade-phase progress status (done/running/died/not_started)."""
    running = launcher.upgrader_running(ctx.host)
    return progress.read_progress(ctx.hostname, "upgrade", api.upgrade_logdir(ctx),
                                  running)["status"]


def launch_side_effect(ctx, mode, ip, perform=None, begin=None):
    """Carry out an accepted launch; returns (status, body dict). Upgrades run a
    before snapshot first (in the background) and then launch; stage launches, or
    any launch without a snapshot runner, spawn nf_upgrade directly — a spawn
    failure (missing/non-executable script, missing config) becomes a clean 500.

    Any stale ctx.state["launch_error"] from a previous launch is cleared up
    front on every accepted launch (whatever the mode/path). For the
    upgrade+runner path, (when ctx.state is a dict) an on_error callback is wired so a launch() failure inside the background
    `then` (audited by begin_upgrade as "launch_error") also lands in
    ctx.state for /api/reports/job to surface to the UI. Losing the race with
    a snapshot already in progress (Busy) is audited as "launch_rejected".
    An accepted launch records its mode as ctx.state["phase"], so the progress
    strip and log follow the phase that was actually launched.

    A launch is refused (409, audited "launch_rejected") while a check fix is
    running. The fix lock is held while spawning / starting the before snapshot,
    so a fix cannot start in between either.

    On every accepted (202) launch, ctx.state["launched_at"] (when ctx.state is
    a dict) is stamped with the current time. api dispatch's /api/checks/fix
    refuses fixes for a short grace period after this, since there is a window
    between an accepted launch and nf_upgrader actually showing up in pgrep
    where a fix could otherwise race the launch it just accepted."""
    perform = perform or perform_launch
    begin = begin or snapjobs.begin_upgrade
    has_state = isinstance(ctx.state, dict)
    if has_state:
        ctx.state.pop("launch_error", None)
    if not api._FIX_LOCK.acquire(False):
        ctx.audit.record("launch_rejected", mode=mode, client=ip, reason="check fix in progress")
        return 409, {"error": "check fix in progress"}
    try:
        status, body = _launch_locked(ctx, mode, ip, perform, begin, has_state)
        if has_state and status == 202:
            ctx.state["launched_at"] = time.time()
        return status, body
    finally:
        api._FIX_LOCK.release()


def _launch_locked(ctx, mode, ip, perform, begin, has_state):
    """launch_side_effect's body, run while holding api._FIX_LOCK."""
    if mode == "upgrade" and ctx.runner is not None:
        kwargs = {"client": ip, "audit": ctx.audit}
        if has_state:
            kwargs["on_error"] = lambda msg: ctx.state.__setitem__("launch_error", msg)
        try:
            before = begin(ctx.runner, lambda: perform(ctx, mode),
                           lambda: upgrade_status(ctx), **kwargs)
        except snapjobs.Busy:
            ctx.audit.record("launch_rejected", mode=mode, client=ip,
                             reason="report snapshot in progress")
            return 409, {"error": "report snapshot in progress"}
        if has_state:
            ctx.state["phase"] = mode
        return 202, {"accepted": True, "mode": mode, "snapshot": before}
    try:
        perform(ctx, mode)
    except Exception as e:
        ctx.audit.record("launch_error", error=str(e), client=ip)
        return 500, {"error": "launch failed: {}".format(e)}
    if has_state:
        ctx.state["phase"] = mode
    return 202, {"accepted": True, "mode": mode}


def _json_response(status, obj):
    return status, [("Content-Type", "application/json")], json.dumps(obj).encode("utf-8")


def safe_dispatch(method, path, query, body, cookies, ip, ctx, dispatch=None, launch=None):
    """api.dispatch plus the launch side effect after an accepted (202) launch.
    Any exception becomes a 500 JSON error (traceback to stderr) so the client
    gets an answer instead of a dropped connection."""
    dispatch = dispatch or api.dispatch
    launch = launch or launch_side_effect
    try:
        status, headers, out = dispatch(method, path, query, body, cookies, ip, ctx)
        if path == "/api/launch" and status == 202:
            mode = json.loads(body.decode("utf-8") or "{}").get("mode")
            status, obj = launch(ctx, mode, ip)
            status, headers, out = _json_response(status, obj)
        return status, headers, out
    except Exception as e:
        sys.stderr.write("nfupgrader: error handling {} {}:\n{}".format(
            method, path, traceback.format_exc()))
        sys.stderr.flush()
        return _json_response(500, {"error": "internal error: {}: {}".format(
            type(e).__name__, e)})


def make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        def _serve_static(self, path):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            safe = posixpath.normpath(name)
            if safe.startswith("..") or safe.startswith("/"):
                self.send_error(403)
                return
            full = os.path.join(ctx.webdir, safe)
            if not os.path.isfile(full):
                self.send_error(404)
                return
            ext = os.path.splitext(full)[1]
            with open(full, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", _CTYPES.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method):
            parsed = urlparse(self.path)
            path = parsed.path
            if method == "GET" and (path == "/" or path.endswith((".js", ".css", ".html"))):
                self._serve_static(path if path != "/" else "/index.html")
                return
            query = parse_qs(parsed.query)
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            cookies = parse_cookies(self.headers.get("Cookie", ""))
            ip = client_ip_from(self.client_address)
            status, headers, out = safe_dispatch(method, path, query, body, cookies, ip, ctx)
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, *a):
            pass  # silence default stderr logging; we have the audit log

    return Handler


def main(argv=None):
    """Bind 127.0.0.1 on $NFU_PORT (or ephemeral) and print the URL+token."""
    here = os.path.dirname(os.path.abspath(__file__))
    webdir = os.path.join(here, "web")
    # run.sh cd's to its own directory, so it passes the caller's directory along.
    start_dir = os.environ.get("NFU_START_DIR") or os.getcwd()
    inclogdir = ensure_dir(resolve_inclogdir(os.environ.get("INCLOGDIR"), start_dir))
    hostname = os.uname()[1]
    # Unset -> decided per request (last launch / state file / newest trace).
    phase = os.environ.get("NFU_PHASE") or None
    tracefile = os.environ.get("NFU_TRACEFILE") or None
    nf_upgrade_path = os.environ.get(
        "NFU_UPGRADE_PATH", os.path.join(here, "..", "nf_upgrade"))
    effective_config = os.environ.get(
        "NFU_EFFECTIVE_CONFIG", os.path.join(inclogdir, "nfupgrader_effective.cfg"))
    user_config = os.environ.get("NFU_USER_CONFIG")
    launch_cwd = resolve_launch_cwd(os.environ.get("NFU_LAUNCH_CWD"), user_config)
    cnc_cnfg_path = os.environ.get("NFU_CNC_CNFG", "/usr/cnc/features/cnc.cnfg")
    checks_path = os.environ.get("NFU_CHECKS", os.path.join(here, "checks.default.json"))
    depot_dirs = resolve_depot_dirs(os.environ.get("NFU_DEPOT_DIRS"), start_dir)
    state = {}
    if user_config:
        state["user_config"] = user_config

    host = Host()
    audit_path = os.path.join(inclogdir, "nfupgrader_audit.jsonl")
    audit = AuditLog(audit_path)
    # A fix interrupted by a prior restart (mid-fix) shows up as a
    # check_fix_start with no matching check_fix; flag it once, for the UI.
    state["interrupted_fix"] = load_interrupted_fix(audit, audit_path)
    reports_path = os.environ.get("NFU_REPORTS", os.path.join(here, "reports.default.json"))
    reports_root = os.path.join(inclogdir, "nfupgrader_reports")
    runner = snapjobs.SnapshotRunner(host, reports_path, reports_root, audit=audit)

    ctx = api.Context(
        host=host, hostname=hostname, inclogdir=inclogdir, phase=phase,
        tracefile=tracefile, auth=TokenAuth(),
        audit=audit,
        webdir=webdir, nf_upgrade_path=nf_upgrade_path,
        effective_config=effective_config, user_config=user_config,
        launch_cwd=launch_cwd, cnc_cnfg_path=cnc_cnfg_path,
        peer_host_factory=lambda name: SshHost(name),
        checks_path=checks_path, state=state, depot_dirs=depot_dirs,
        reports_path=reports_path, reports_root=reports_root, runner=runner,
    )

    port = int(os.environ.get("NFU_PORT", "0"))
    bind = os.environ.get("NFU_BIND", "127.0.0.1")
    url_host = resolve_url_host(bind, os.environ.get("NFU_URL_HOST"), hostname)

    server = ThreadingHTTPServer((bind, port), make_handler(ctx))
    real_port = server.server_address[1]
    url = "http://{}:{}/auth?token={}".format(url_host, real_port, ctx.auth.token)
    print("nfupgrader listening on {}:{}".format(bind, real_port))
    if bind in ("127.0.0.1", "localhost"):
        print("Loopback only. From your workstation, tunnel first, e.g.:")
        print("    ssh -L {p}:127.0.0.1:{p} <this-host>   then browse:".format(p=real_port))
        print("    http://127.0.0.1:{}/auth?token={}".format(real_port, ctx.auth.token))
    else:
        print("WARNING: bound to {} — the URL/token cross the network in plaintext HTTP.".format(bind))
        print("Open (single-use):\n    {}".format(url))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

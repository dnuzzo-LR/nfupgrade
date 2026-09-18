"""httpd.py — thin http.server adapter over api.dispatch, plus main() that binds
loopback and prints the one-time access URL. Serves static files from web/."""
import json
import os
import posixpath
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from nfupgrader import api, launcher
from nfupgrader.auth import TokenAuth
from nfupgrader.audit import AuditLog
from nfupgrader.host import Host

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
            status, headers, out = api.dispatch(method, path, query, body, cookies, ip, ctx)
            # Perform the launch side effect after a recorded 202.
            if path == "/api/launch" and status == 202:
                mode = json.loads(body.decode() or "{}").get("mode")
                launcher.launch(ctx.nf_upgrade_path, ctx.effective_config, mode, cwd=ctx.webdir)
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
    inclogdir = os.environ.get("INCLOGDIR", "/usr/cnc/logs")
    hostname = os.uname()[1]
    phase = os.environ.get("NFU_PHASE", "stage")
    tracefile = os.environ.get(
        "NFU_TRACEFILE",
        os.path.join(inclogdir, "{}_inc_{}".format(phase, hostname)),
    )
    nf_upgrade_path = os.environ.get(
        "NFU_UPGRADE_PATH", os.path.join(here, "..", "nf_upgrade"))
    effective_config = os.environ.get(
        "NFU_EFFECTIVE_CONFIG", os.path.join(inclogdir, "nfupgrader_effective.cfg"))

    ctx = api.Context(
        host=Host(), hostname=hostname, inclogdir=inclogdir, phase=phase,
        tracefile=tracefile, auth=TokenAuth(),
        audit=AuditLog(os.path.join(inclogdir, "nfupgrader_audit.jsonl")),
        webdir=webdir, nf_upgrade_path=nf_upgrade_path,
        effective_config=effective_config,
    )

    port = int(os.environ.get("NFU_PORT", "0"))
    server = HTTPServer(("127.0.0.1", port), make_handler(ctx))
    real_port = server.server_address[1]
    url = "http://127.0.0.1:{}/auth?token={}".format(real_port, ctx.auth.token)
    print("nfupgrader listening on 127.0.0.1:{}".format(real_port))
    print("Open (single-use):\n    {}".format(url))
    print("From your workstation, tunnel first, e.g.:")
    print("    ssh -L {p}:127.0.0.1:{p} <this-host>   then browse the URL above".format(p=real_port))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

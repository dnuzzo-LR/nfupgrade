"""bootmenu_ws.py -- the /ws/bootmenu WebSocket endpoint: connects one browser
terminal to the tool's BootMenuSession.

Frames are JSON text (see the constraints): client -> server start / input /
resize; server -> client output / status / exit / detached / error.
Keystrokes (input data) are never audited.
"""
import errno
import json
import select
import socket
import time

from nfupgrader import api, websocket

#: Seconds a single frame write may take before the peer is treated as dead.
SEND_TIMEOUT = 10.0
#: Largest terminal dimension accepted from a resize/start message.
MAX_DIM = 1000

_NOT_RUNNING = "no boot menu session is running"


def refusal(headers, cookies, ip, ctx):
    """HTTP status refusing a /ws/bootmenu upgrade, or None to accept: 403 if
    the nfu_sid session is not valid for ip or Origin does not match Host;
    400 if the request is not a valid WebSocket upgrade."""
    if not api._needs_session(cookies, ip, ctx) or not websocket.origin_ok(headers):
        return 403
    if websocket.upgrade_error(headers):
        return 400
    return None


class TimedWriter(object):
    """A write-only file over a blocking socket whose writes are bounded by
    SEND_TIMEOUT (non-blocking sends + select), so reads on the same socket
    can stay blocking with no timeout -- an idle browser is not a dead one.

    A write that times out or fails shuts the socket down (both ways), which
    also wakes the request thread blocked reading from it."""

    def __init__(self, sock, timeout=SEND_TIMEOUT):
        self.sock = sock
        self.timeout = timeout

    def write(self, data):
        view = memoryview(data)
        deadline = time.time() + self.timeout
        try:
            while view:
                try:
                    n = self.sock.send(view, socket.MSG_DONTWAIT)
                    view = view[n:]
                    continue
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                        raise
                left = deadline - time.time()
                if left <= 0:
                    raise socket.timeout("websocket send timed out")
                select.select([], [self.sock], [], left)
        except OSError:
            self.kill()
            raise
        return len(data)

    def flush(self):
        pass

    def kill(self):
        """Shut the socket down both ways (never raises)."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _dim(value):
    """value as a terminal dimension (int 1..MAX_DIM), or None if invalid."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 1 <= value <= MAX_DIM:
        return value
    return None


class _Conn(object):
    """One browser connection's view of the session: whether it is the
    attached client, and the send callback handed to session.attach()."""

    def __init__(self, sock, writer):
        self.sock = sock
        self.writer = writer
        self.token = None
        self.taken_over = False

    def send(self, frame):
        """session.attach() callback. Runs under the session lock: only a
        bounded socket write, never session.write()/resize(). Exceptions
        propagate (the session then detaches us)."""
        self.sock.send_json(frame)
        if frame.get("type") == "detached":
            # Another browser took over: we are no longer attached. Close
            # this connection so its reader returns and it can't type.
            self.taken_over = True
            self.sock.close()
            if self.writer is not None:
                self.writer.kill()

    def attached(self):
        return self.token is not None and not self.taken_over

    def error(self, message):
        self.sock.send_json({"type": "error", "message": message})


def _handle(conn, msg, session, ctx, client_ip):
    """Act on one decoded client message. Ignored once another browser has
    taken over this connection (it is being closed)."""
    if conn.taken_over:
        return
    kind = msg.get("type")
    if kind == "start":
        avail = api.bootmenu_availability(ctx)
        if session is None:
            reason = "the boot menu is not available"
        elif session.running():
            reason = "a boot menu session is already running"
        elif not avail["available"]:
            reason = avail["reason"]
        else:
            reason = None
        if reason:
            ctx.audit.record("bootmenu_refused", reason=reason, client=client_ip)
            conn.error(reason)
            return
        cols = _dim(msg.get("cols")) or 80
        rows = _dim(msg.get("rows")) or 24
        try:
            session.start(cols, rows)
        except RuntimeError as exc:
            ctx.audit.record("bootmenu_refused", reason=str(exc), client=client_ip)
            conn.error(str(exc))
            return
        ctx.audit.record("bootmenu_start", client=client_ip)
        # start() already sent "running" to us if we are the attached client.
        if not conn.attached():
            conn.token = session.attach(conn.send)
        return
    if kind in ("input", "resize"):
        if session is None or not session.running():
            conn.error(_NOT_RUNNING)
            return
        if not conn.attached():
            conn.error("the boot menu is open in another browser")
            return
        if kind == "input":
            data = msg.get("data")
            if not isinstance(data, str):
                conn.error("input data must be a string")
                return
            try:
                session.write(data)
            except UnicodeEncodeError:
                conn.error("input data is not valid text")
            except RuntimeError as exc:
                conn.error(str(exc))
            return
        cols, rows = _dim(msg.get("cols")), _dim(msg.get("rows"))
        if cols is None or rows is None:
            conn.error("cols and rows must be integers from 1 to {}".format(MAX_DIM))
            return
        try:
            session.resize(cols, rows)
        except OSError as exc:
            conn.error("resize failed: {}".format(exc))
        return
    conn.error("unknown message type")


def serve(sock, ctx, client_ip, writer=None):
    """Run one /ws/bootmenu connection until the browser goes away.

    On connect: attach to a running/ended session (scrollback replay +
    status), else send status "none". Then handle start/input/resize
    messages; a bad message gets an error frame and the loop continues. EOF,
    a close frame, a protocol violation or a socket error ends the loop:
    detach and return. writer, if given, is the TimedWriter under sock (shut
    down when the connection is taken over by another browser)."""
    session = api._bootmenu_session(ctx)
    conn = _Conn(sock, writer)
    try:
        if session is not None and session.state()["state"] in ("running", "ended"):
            # Audited first so the record precedes anything the browser sees.
            ctx.audit.record("bootmenu_attach", client=client_ip)
            conn.token = session.attach(conn.send)
        else:
            sock.send_json({"type": "status", "state": "none"})
        while True:
            try:
                text = sock.recv()
            except (websocket.ProtocolError, OSError):
                break
            if text is None:
                break
            try:
                msg = json.loads(text)
            except ValueError:
                conn.error("bad json")
                continue
            if not isinstance(msg, dict):
                conn.error("message must be a JSON object")
                continue
            _handle(conn, msg, session, ctx, client_ip)
    except OSError:
        pass  # the browser is gone mid-send
    finally:
        if session is not None and conn.token is not None:
            session.detach(conn.token)
            ctx.audit.record("bootmenu_detach", client=client_ip)
        sock.close()

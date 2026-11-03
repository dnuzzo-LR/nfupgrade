"""Tests for the /ws/bootmenu WebSocket endpoint (bootmenu_ws + httpd wiring).

A real ThreadingHTTPServer on 127.0.0.1 with a raw-socket WebSocket client.
The session runs a stand-in shell command (never su / new_boot_nms)."""
import base64
import json
import os
import shutil
import signal
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock

from nfupgrader import bootmenu, bootmenu_ws, httpd, websocket
from tests.test_api import make_ctx, install_ctx

#: Stand-in menu: echoes its first line back, exits 0 on the second.
MENU_ARGV = ["/bin/sh", "-c", "read a; echo got:$a; read b"]
DEADLINE = 10.0


def _audit_events(ctx):
    try:
        with open(ctx.audit.path) as fh:
            lines = fh.read().splitlines()
    except (IOError, OSError):
        return []
    return [json.loads(l) for l in lines]


class WsClient(object):
    """Minimal raw-socket WebSocket client: handshake, masked text frames out,
    unmasked frames in."""

    def __init__(self, port, cookie=None, origin=None, key=None, upgrade="websocket"):
        self.port = port
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=DEADLINE)
        self.key = key or base64.b64encode(os.urandom(16)).decode("ascii")
        host = "127.0.0.1:{}".format(port)
        lines = ["GET /ws/bootmenu HTTP/1.1", "Host: " + host,
                 "Upgrade: " + upgrade, "Connection: Upgrade",
                 "Sec-WebSocket-Version: 13", "Sec-WebSocket-Key: " + self.key]
        lines.append("Origin: " + (origin if origin is not None else "http://" + host))
        if cookie:
            lines.append("Cookie: nfu_sid=" + cookie)
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        self.status_line = lines[0]
        self.headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                self.headers[k.strip().lower()] = v.strip()

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv_frame(self):
        """(opcode, payload bytes) of the next server frame."""
        b0, b1 = struct.unpack("!BB", self._read(2))
        n = b1 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", self._read(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", self._read(8))
        return b0 & 0x0F, self._read(n)

    def recv_json(self):
        """Next text frame as a dict (skips nothing: a non-text frame fails)."""
        op, payload = self.recv_frame()
        if op != websocket.OP_TEXT:
            raise AssertionError("expected text frame, got opcode {}".format(op))
        return json.loads(payload.decode("utf-8"))

    def wait_for(self, pred):
        """Read frames until pred(frame) is true; return (frame, seen)."""
        seen = []
        deadline = time.time() + DEADLINE
        while time.time() < deadline:
            frame = self.recv_json()
            seen.append(frame)
            if pred(frame):
                return frame, seen
        raise AssertionError("timed out; frames seen: {!r}".format(seen))

    def send_raw(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(bytearray(payload)))
        self.sock.sendall(head + mask + body)

    def send_json(self, obj):
        self.send_raw(websocket.OP_TEXT, json.dumps(obj).encode("utf-8"))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _wait_until(pred, timeout=DEADLINE):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class WsServerCase(unittest.TestCase):
    def make_ctx(self):
        return make_ctx(self.tmp, running=False)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # No other boot menus on this box as far as availability is concerned
        # (this box may have netFLEX installed; never look at its real menus).
        p = mock.patch("nfupgrader.bootmenu.classify_others", return_value=("none", []))
        p.start()
        self.addCleanup(p.stop)
        self.ctx = self.make_ctx()
        self.session = bootmenu.BootMenuSession(
            argv=MENU_ARGV, kill_names={"sh"},
            on_exit=httpd.bootmenu_end_auditor(self.ctx.audit))
        self.ctx.state["bootmenu"] = self.session
        self.sid = self.ctx.auth.redeem("TOK", "127.0.0.1")
        self.server = httpd.ThreadingHTTPServer(("127.0.0.1", 0), httpd.make_handler(self.ctx))
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever)
        t.daemon = True
        t.start()
        self.clients = []
        self.addCleanup(self._teardown)

    def _teardown(self):
        for c in self.clients:
            c.close()
        if self.session.running():
            self.session.end()
        self.server.shutdown()
        self.server.server_close()

    def connect(self, **kw):
        kw.setdefault("cookie", self.sid)
        c = WsClient(self.port, **kw)
        self.clients.append(c)
        return c


class TestRefusal(WsServerCase):
    def test_ws_refused_bad_origin_and_no_cookie(self):
        c = self.connect(cookie="")
        self.assertTrue(c.status_line.startswith("HTTP/1.0 403"), c.status_line)
        self.assertNotIn("sec-websocket-accept", c.headers)
        c = self.connect(origin="http://evil")
        self.assertTrue(c.status_line.startswith("HTTP/1.0 403"), c.status_line)
        self.assertNotIn("sec-websocket-accept", c.headers)

    def test_ws_bad_upgrade_with_valid_auth_is_400(self):
        c = self.connect(upgrade="h2c")
        self.assertTrue(c.status_line.startswith("HTTP/1.0 400"), c.status_line)
        self.assertNotIn("sec-websocket-accept", c.headers)
        self.assertNotIn(b"101", c.buf)

    def test_refusal_function(self):
        host = "127.0.0.1:1"
        good = {"Host": host, "Origin": "http://" + host, "Upgrade": "websocket",
                "Connection": "keep-alive, Upgrade", "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "abc"}
        cookies = {"nfu_sid": self.sid}
        self.assertIsNone(bootmenu_ws.refusal(good, cookies, "127.0.0.1", self.ctx))
        self.assertEqual(bootmenu_ws.refusal(good, {}, "127.0.0.1", self.ctx), 403)
        self.assertEqual(bootmenu_ws.refusal(good, cookies, "10.0.0.1", self.ctx), 403)
        bad = dict(good, Origin="http://evil")
        self.assertEqual(bootmenu_ws.refusal(bad, cookies, "127.0.0.1", self.ctx), 403)
        bad = dict(good, Upgrade="h2c")
        self.assertEqual(bootmenu_ws.refusal(bad, cookies, "127.0.0.1", self.ctx), 400)


class TestSession(WsServerCase):
    def test_start_input_output_exit(self):
        c = self.connect()
        self.assertTrue(c.status_line.startswith("HTTP/1.1 101"), c.status_line)
        self.assertEqual(c.headers.get("upgrade", "").lower(), "websocket")
        self.assertEqual(c.headers.get("sec-websocket-accept"), websocket.accept_key(c.key))
        self.assertEqual(c.recv_json(), {"type": "status", "state": "none"})
        c.send_json({"type": "start"})
        c.wait_for(lambda f: f == {"type": "status", "state": "running"})
        c.send_json({"type": "input", "data": "hi\r"})
        c.wait_for(lambda f: f["type"] == "output" and "got:hi" in f["data"])
        c.send_json({"type": "input", "data": "\r"})
        frame, _ = c.wait_for(lambda f: f["type"] == "exit")
        self.assertEqual(frame["code"], 0)
        events = [e["event"] for e in _audit_events(self.ctx)]
        self.assertIn("bootmenu_start", events)

    def test_reattach_replays(self):
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "start"})
        c.send_json({"type": "input", "data": "hi\r"})
        c.wait_for(lambda f: f["type"] == "output" and "got:hi" in f["data"])
        c.close()
        _wait_until(lambda: "bootmenu_detach" in
                    [e["event"] for e in _audit_events(self.ctx)])
        c2 = self.connect()
        first = c2.recv_json()
        self.assertEqual(first["type"], "output")
        self.assertTrue(first.get("replay"))
        self.assertIn("got:", first["data"])
        self.assertEqual(c2.recv_json(), {"type": "status", "state": "running"})
        events = [e["event"] for e in _audit_events(self.ctx)]
        self.assertIn("bootmenu_attach", events)
        self.assertIn("bootmenu_detach", events)

    def test_takeover_detaches_first_and_ignores_its_input(self):
        a = self.connect()
        a.recv_json()
        a.send_json({"type": "start"})
        a.wait_for(lambda f: f == {"type": "status", "state": "running"})
        b = self.connect()
        b.wait_for(lambda f: f == {"type": "status", "state": "running"})
        frame, _ = a.wait_for(lambda f: f["type"] == "detached")
        self.assertEqual(frame["reason"], "opened elsewhere")
        b.send_json({"type": "input", "data": "yo\r"})
        b.wait_for(lambda f: f["type"] == "output" and "got:yo" in f["data"])

    def test_input_without_session_is_error(self):
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "input", "data": "x"})
        self.assertEqual(c.recv_json(),
                         {"type": "error", "message": "no boot menu session is running"})
        c.send_json({"type": "resize", "cols": 100, "rows": 30})
        self.assertEqual(c.recv_json(),
                         {"type": "error", "message": "no boot menu session is running"})
        c.send_json({"type": "start"})
        c.wait_for(lambda f: f == {"type": "status", "state": "running"})
        self.assertTrue(self.session.running())

    def test_bad_messages_are_errors_and_loop_survives(self):
        c = self.connect()
        c.recv_json()
        c.send_raw(websocket.OP_TEXT, b"not json")
        self.assertEqual(c.recv_json()["type"], "error")
        c.send_json({"type": "bogus"})
        self.assertEqual(c.recv_json()["type"], "error")
        c.send_json([1, 2])
        self.assertEqual(c.recv_json()["type"], "error")
        c.send_json({"type": "start"})
        c.wait_for(lambda f: f == {"type": "status", "state": "running"})
        # non-string data, lone surrogate, out-of-range / non-int sizes
        c.send_json({"type": "input", "data": 5})
        self.assertEqual(c.recv_json()["type"], "error")
        c.send_raw(websocket.OP_TEXT, b'{"type":"input","data":"\\ud800"}')
        self.assertEqual(c.recv_json()["type"], "error")
        for cols, rows in ((-1, 24), (80, 70000), ("80", 24), (80, None), (True, 24)):
            c.send_json({"type": "resize", "cols": cols, "rows": rows})
            self.assertEqual(c.recv_json()["type"], "error", (cols, rows))
        c.send_json({"type": "resize", "cols": 120, "rows": 40})
        c.send_json({"type": "input", "data": "ok\r"})
        c.wait_for(lambda f: f["type"] == "output" and "got:ok" in f["data"])

    def test_keystrokes_never_audited(self):
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "start"})
        c.send_json({"type": "input", "data": "sekrit\r"})
        c.wait_for(lambda f: f["type"] == "output" and "got:sekrit" in f["data"])
        with open(self.ctx.audit.path) as fh:
            self.assertNotIn("sekrit", fh.read())

    def _ends(self):
        return [e for e in _audit_events(self.ctx) if e["event"] == "bootmenu_end"]

    def test_natural_exit_audited_once(self):
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "start"})
        c.send_json({"type": "input", "data": "a\r"})
        c.send_json({"type": "input", "data": "b\r"})
        c.wait_for(lambda f: f["type"] == "exit")
        self.assertTrue(_wait_until(lambda: len(self._ends()) >= 1))
        ends = self._ends()
        self.assertEqual(len(ends), 1, ends)
        self.assertEqual(ends[0]["exit_code"], 0)
        self.assertIs(ends[0]["forced"], False)
        self.assertIsNone(ends[0]["reason"])

    def test_end_session_audited_once(self):
        from nfupgrader import api
        self.session.start()
        status, _, _ = api.dispatch("POST", "/api/bootmenu/end", {}, b"{}",
                                    {"nfu_sid": self.sid}, "127.0.0.1", self.ctx)
        self.assertEqual(status, 200)
        self.assertFalse(self.session.running())
        ends = self._ends()
        self.assertEqual(len(ends), 1, ends)
        self.assertIs(ends[0]["forced"], True)
        self.assertIsNone(ends[0]["reason"])
        self.assertEqual(ends[0]["exit_code"], 128 + signal.SIGKILL)

    def test_shutdown_bootmenu_ends_session(self):
        self.session.start()
        self.assertTrue(self.session.running())
        httpd.shutdown_bootmenu(self.ctx)
        self.assertFalse(self.session.running())
        ends = self._ends()
        self.assertEqual(len(ends), 1, ends)
        self.assertEqual(ends[0]["reason"], "tool_stop")
        self.assertIs(ends[0]["forced"], True)
        self.assertEqual(ends[0]["exit_code"], 128 + signal.SIGKILL)
        self.assertNotIn("bootmenu_end_timeout",
                         [e["event"] for e in _audit_events(self.ctx)])

    def test_shutdown_bootmenu_timeout_audited(self):
        self.session.start()
        self.session.kill_names = {"nothing-matches"}
        httpd.shutdown_bootmenu(self.ctx)
        self.assertTrue(self.session.running())
        self.assertIn("bootmenu_end_timeout",
                      [e["event"] for e in _audit_events(self.ctx)])
        self.assertEqual(self._ends(), [])
        self.session.kill_names = {"sh"}

    def test_shutdown_bootmenu_noop_without_session(self):
        httpd.shutdown_bootmenu(self.ctx)
        self.ctx.state.pop("bootmenu")
        httpd.shutdown_bootmenu(self.ctx)
        self.assertEqual([e for e in _audit_events(self.ctx) if e["event"] == "bootmenu_end"],
                         [])


class TestStartGuards(WsServerCase):
    """I1: a start can't race a launch, a check fix or a report snapshot."""

    def _refused(self, message):
        c = self.connect()
        self.assertEqual(c.recv_json(), {"type": "status", "state": "none"})
        c.send_json({"type": "start"})
        self.assertEqual(c.recv_json(), {"type": "error", "message": message})
        self.assertFalse(self.session.running())
        refused = [e for e in _audit_events(self.ctx) if e["event"] == "bootmenu_refused"]
        self.assertEqual([e["reason"] for e in refused], [message])
        self.assertNotIn("bootmenu_start", [e["event"] for e in _audit_events(self.ctx)])

    def test_start_refused_while_fix_lock_held(self):
        from nfupgrader import api
        self.assertTrue(api._FIX_LOCK.acquire(False))
        try:
            self._refused("a check fix or launch is in progress")
        finally:
            api._FIX_LOCK.release()

    def test_start_refused_within_launch_grace(self):
        from nfupgrader import api
        self.ctx.state["launched_at"] = api._now() - 5
        self._refused("an upgrade or install was just launched")

    def test_start_allowed_after_launch_grace(self):
        from nfupgrader import api
        self.ctx.state["launched_at"] = api._now() - api.FIX_LAUNCH_GRACE - 1
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "start"})
        c.wait_for(lambda f: f == {"type": "status", "state": "running"})

    def test_start_refused_while_snapshot_runs(self):
        with mock.patch("nfupgrader.api._snapshot_busy", return_value=True):
            self._refused("a report snapshot is in progress")

    def test_start_releases_fix_lock(self):
        from nfupgrader import api
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "start"})
        c.wait_for(lambda f: f == {"type": "status", "state": "running"})
        self.assertFalse(api.fix_in_progress())

    def test_attach_via_start_is_audited(self):
        # M5: attaching through start writes bootmenu_attach like a reattach.
        c = self.connect()
        c.recv_json()
        c.send_json({"type": "start"})
        c.wait_for(lambda f: f == {"type": "status", "state": "running"})
        c.close()
        self.assertTrue(_wait_until(lambda: "bootmenu_detach" in
                                    [e["event"] for e in _audit_events(self.ctx)]))
        events = [e["event"] for e in _audit_events(self.ctx)]
        self.assertEqual(events.count("bootmenu_attach"), 1, events)
        self.assertLess(events.index("bootmenu_start"), events.index("bootmenu_attach"))
        self.assertLess(events.index("bootmenu_attach"), events.index("bootmenu_detach"))


class TestUnavailable(WsServerCase):
    def make_ctx(self):
        return install_ctx(self.tmp, installed=False)

    def test_start_refused_when_unavailable(self):
        c = self.connect()
        self.assertEqual(c.recv_json(), {"type": "status", "state": "none"})
        c.send_json({"type": "start"})
        self.assertEqual(c.recv_json(),
                         {"type": "error", "message": "netFLEX is not installed"})
        self.assertFalse(self.session.running())
        refused = [e for e in _audit_events(self.ctx) if e["event"] == "bootmenu_refused"]
        self.assertEqual(refused[0]["reason"], "netFLEX is not installed")


class TestTimedWriter(unittest.TestCase):
    def test_send_to_unread_peer_times_out_and_shuts_down(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        w = bootmenu_ws.TimedWriter(a, timeout=0.3)
        self.assertEqual(w.write(b"x" * 10), 10)
        start = time.time()
        with self.assertRaises(OSError):
            for _ in range(10000):
                w.write(b"y" * 65536)
        self.assertLess(time.time() - start, 5)
        # shut down: the peer sees EOF once the queued bytes are drained
        b.settimeout(5)
        while b.recv(1 << 20):
            pass


class TestServeUntilStopped(unittest.TestCase):
    """main()'s serve loop ends the boot menu however serve_forever exits."""

    class FakeServer(object):
        def __init__(self, exc):
            self.exc = exc
            self.closed = False

        def serve_forever(self):
            raise self.exc

        def server_close(self):
            self.closed = True

    def run_with(self, exc):
        server = self.FakeServer(exc)
        old_term = signal.getsignal(signal.SIGTERM)
        old_int = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGTERM, old_term)
        self.addCleanup(signal.signal, signal.SIGINT, old_int)
        with mock.patch("nfupgrader.httpd.shutdown_bootmenu") as sb:
            try:
                httpd.serve_until_stopped(server, "CTX")
            except BaseException as e:
                raised = e
            else:
                raised = None
        sb.assert_called_once_with("CTX")
        self.assertTrue(server.closed)
        return raised

    def test_keyboardinterrupt_is_a_clean_stop(self):
        self.assertIsNone(self.run_with(KeyboardInterrupt()))

    def test_systemexit_still_ends_menu(self):
        self.assertIsInstance(self.run_with(SystemExit(2)), SystemExit)

    def test_error_still_ends_menu(self):
        self.assertIsInstance(self.run_with(OSError("boom")), OSError)


class TestSignals(unittest.TestCase):
    def test_sigterm_handler_raises_keyboardinterrupt(self):
        old_term = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, old_term)
        with self.assertRaises(KeyboardInterrupt):
            httpd._on_sigterm(signal.SIGTERM, None)
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)

    def test_install_signals(self):
        old_hup = signal.getsignal(signal.SIGHUP)
        old_term = signal.getsignal(signal.SIGTERM)
        try:
            httpd.install_signals()
            self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_IGN)
            self.assertEqual(signal.getsignal(signal.SIGTERM), httpd._on_sigterm)
        finally:
            signal.signal(signal.SIGHUP, old_hup)
            signal.signal(signal.SIGTERM, old_term)


if __name__ == "__main__":
    unittest.main()

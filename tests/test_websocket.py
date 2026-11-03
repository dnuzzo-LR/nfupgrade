import io, json, struct, unittest
from nfupgrader import websocket as ws

def client_frame(opcode, payload, fin=True, mask=b"\x01\x02\x03\x04"):
    b0 = (0x80 if fin else 0) | opcode
    n = len(payload)
    if n < 126: hdr = struct.pack("!BB", b0, 0x80 | n)
    elif n < 65536: hdr = struct.pack("!BBH", b0, 0x80 | 126, n)
    else: hdr = struct.pack("!BBQ", b0, 0x80 | 127, n)
    return hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))

class TestHandshake(unittest.TestCase):
    def test_rfc_example(self):
        self.assertEqual(ws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
    def test_upgrade_error(self):
        good = {"Upgrade": "websocket", "Connection": "keep-alive, Upgrade",
                "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "x"}
        self.assertIsNone(ws.upgrade_error(good))
        for k in ("Upgrade", "Sec-WebSocket-Key", "Sec-WebSocket-Version"):
            bad = dict(good); bad.pop(k)
            self.assertIsNotNone(ws.upgrade_error(bad), k)
    def test_origin(self):
        self.assertTrue(ws.origin_ok({"Origin": "http://h1:8099", "Host": "h1:8099"}))
        self.assertFalse(ws.origin_ok({"Origin": "http://evil:8099", "Host": "h1:8099"}))
        self.assertFalse(ws.origin_ok({"Host": "h1:8099"}))

class TestFrames(unittest.TestCase):
    def sock(self, data):
        out = io.BytesIO()
        return ws.WebSocket(io.BytesIO(data), out), out
    def test_text_and_lengths(self):
        for n in (5, 200, 70000):
            s, _ = self.sock(client_frame(ws.OP_TEXT, b"a" * n))
            self.assertEqual(s.recv(), "a" * n)
    def test_continuation(self):
        s, _ = self.sock(client_frame(ws.OP_TEXT, b"ab", fin=False) + client_frame(ws.OP_CONT, b"cd"))
        self.assertEqual(s.recv(), "abcd")
    def test_ping_answered_then_message(self):
        s, out = self.sock(client_frame(ws.OP_PING, b"p") + client_frame(ws.OP_TEXT, b"hi"))
        self.assertEqual(s.recv(), "hi")
        self.assertEqual(out.getvalue()[:3], bytes([0x80 | ws.OP_PONG, 1]) + b"p")
    def test_close_and_eof(self):
        s, _ = self.sock(client_frame(ws.OP_CLOSE, struct.pack("!H", 1000)))
        self.assertIsNone(s.recv())
        s, _ = self.sock(b"")
        self.assertIsNone(s.recv())
    def test_close_echoed_once(self):
        s, out = self.sock(client_frame(ws.OP_CLOSE, struct.pack("!H", 1000)))
        self.assertIsNone(s.recv())
        expected = ws.encode_frame(ws.OP_CLOSE, struct.pack("!H", 1000))
        self.assertEqual(out.getvalue(), expected)
        s.close()  # already sent a close frame: no-op
        self.assertEqual(out.getvalue(), expected)
    def test_oversized_control_frame_rejected(self):
        s, _ = self.sock(client_frame(ws.OP_PING, b"p" * 126))
        with self.assertRaises(ws.ProtocolError): s.recv()
    def test_fragmented_control_frame_rejected(self):
        s, _ = self.sock(client_frame(ws.OP_PING, b"p", fin=False))
        with self.assertRaises(ws.ProtocolError): s.recv()
    def test_rsv_bit_rejected(self):
        payload = b"hi"
        mask = b"\x01\x02\x03\x04"
        b0 = 0x80 | 0x40 | ws.OP_TEXT  # FIN + RSV1 + opcode
        hdr = struct.pack("!BB", b0, 0x80 | len(payload))
        frame = hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        s, _ = self.sock(frame)
        with self.assertRaises(ws.ProtocolError): s.recv()
    def test_unmasked_and_oversize_rejected(self):
        s, _ = self.sock(ws.encode_frame(ws.OP_TEXT, b"x"))
        with self.assertRaises(ws.ProtocolError): s.recv()
        s, _ = self.sock(client_frame(ws.OP_TEXT, b"a" * (ws.MAX_MESSAGE + 1)))
        with self.assertRaises(ws.ProtocolError): s.recv()
    def test_encode_and_send_json(self):
        self.assertEqual(ws.encode_frame(ws.OP_TEXT, b"hi"), b"\x81\x02hi")
        self.assertEqual(ws.encode_frame(ws.OP_TEXT, b"a" * 300)[:4], b"\x81\x7e\x01\x2c")
        s, out = self.sock(b"")
        s.send_json({"type": "output", "data": "x"})
        self.assertEqual(json.loads(out.getvalue()[2:].decode()), {"type": "output", "data": "x"})

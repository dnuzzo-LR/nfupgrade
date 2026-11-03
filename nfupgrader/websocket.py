"""websocket.py — RFC 6455 WebSocket handshake and framing, stdlib-only.

Pure protocol layer: no socket creation, no threading beyond a send lock,
no http.server coupling. A later task wires this into the http.server
request handler.
"""

import base64
import hashlib
import json
import struct
import threading

#: Magic GUID used to compute the Sec-WebSocket-Accept handshake response
#: (RFC 6455 section 1.3).
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0     #: Continuation frame opcode.
OP_TEXT = 1     #: Text frame opcode.
OP_BINARY = 2   #: Binary frame opcode.
OP_CLOSE = 8    #: Connection close opcode.
OP_PING = 9     #: Ping opcode.
OP_PONG = 10    #: Pong opcode.

#: Maximum reassembled message size, in bytes (1 MiB).
MAX_MESSAGE = 1048576


class ProtocolError(Exception):
    """An unmasked client frame, an oversize message, or a bad opcode."""
    pass


def accept_key(key):
    """Compute the Sec-WebSocket-Accept value for a client handshake key:
    base64(sha1(key + GUID)), per RFC 6455."""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def upgrade_error(headers):
    """Validate a WebSocket upgrade request's headers (any mapping with
    case-insensitive .get, e.g. http.server's HTTPMessage). Returns None if
    Upgrade: websocket, Connection's comma list contains "upgrade"
    case-insensitively, Sec-WebSocket-Version is 13, and Sec-WebSocket-Key is
    present; otherwise a short string naming the problem."""
    upgrade = headers.get("Upgrade")
    if not upgrade or upgrade.strip().lower() != "websocket":
        return "missing or invalid Upgrade header"

    connection = headers.get("Connection")
    if not connection:
        return "missing Connection header"
    tokens = [t.strip().lower() for t in connection.split(",")]
    if "upgrade" not in tokens:
        return "Connection header does not request upgrade"

    if headers.get("Sec-WebSocket-Version") != "13":
        return "unsupported Sec-WebSocket-Version"

    if not headers.get("Sec-WebSocket-Key"):
        return "missing Sec-WebSocket-Key"

    return None


def origin_ok(headers):
    """True if Origin is present and its host[:port] equals the Host header
    exactly; False otherwise (including when Origin is absent)."""
    origin = headers.get("Origin")
    host = headers.get("Host")
    if not origin or not host:
        return False
    # Strip the scheme ("http://" / "https://") to get host[:port].
    origin_host = origin.split("://", 1)[-1]
    return origin_host == host


def encode_frame(opcode, payload):
    """Encode a single unmasked server-to-client frame: FIN set, 7/16/64-bit
    length per RFC 6455."""
    n = len(payload)
    b0 = 0x80 | (opcode & 0x0F)
    if n < 126:
        header = struct.pack("!BB", b0, n)
    elif n < 65536:
        header = struct.pack("!BBH", b0, 126, n)
    else:
        header = struct.pack("!BBQ", b0, 127, n)
    return header + payload


class WebSocket(object):
    """A server-side WebSocket connection over buffered socket files
    (rfile/wfile, as handed to an http.server request handler).

    Reads client frames (always masked, per RFC 6455) and writes server
    frames (always unmasked). Pings are answered with pongs transparently
    inside recv(). Sends are serialized by an internal lock for thread
    safety."""

    def __init__(self, rfile, wfile):
        self.rfile = rfile
        self.wfile = wfile
        self.closed = False
        self._lock = threading.Lock()

    def _read_exact(self, n):
        """Read exactly n bytes from rfile, looping on short reads. Returns
        None on EOF (a read returned nothing) before n bytes were obtained."""
        if n == 0:
            return b""
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_frame(self):
        """Read and unmask a single client frame. Returns (fin, opcode,
        payload_bytes), or None on EOF. Raises ProtocolError if the frame is
        unmasked or its declared length exceeds MAX_MESSAGE."""
        head = self._read_exact(2)
        if head is None:
            return None
        b0, b1 = struct.unpack("!BB", head)
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F

        if not masked:
            raise ProtocolError("client frame must be masked")

        if length == 126:
            ext = self._read_exact(2)
            if ext is None:
                return None
            (length,) = struct.unpack("!H", ext)
        elif length == 127:
            ext = self._read_exact(8)
            if ext is None:
                return None
            (length,) = struct.unpack("!Q", ext)

        if length > MAX_MESSAGE:
            raise ProtocolError("frame payload exceeds MAX_MESSAGE")

        mask = self._read_exact(4)
        if mask is None:
            return None
        raw = self._read_exact(length)
        if raw is None:
            return None
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(bytearray(raw)))
        return fin, opcode, payload

    def recv(self):
        """Receive the next complete text message, reassembling
        continuation frames. Answers ping frames with pong frames
        transparently. Returns the decoded text (UTF-8, errors replaced),
        or None on a close frame or EOF. Raises ProtocolError on an
        unmasked client frame, an oversize message, or a bad opcode."""
        parts = []
        total = 0
        started = False
        while True:
            frame = self._read_frame()
            if frame is None:
                self.closed = True
                return None
            fin, opcode, payload = frame

            if opcode == OP_CLOSE:
                self.closed = True
                return None
            elif opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            elif opcode == OP_PONG:
                continue
            elif opcode == OP_TEXT:
                if started:
                    raise ProtocolError("unexpected new message mid-fragmentation")
                started = True
            elif opcode == OP_CONT:
                if not started:
                    raise ProtocolError("continuation frame without initial frame")
            else:
                raise ProtocolError("unsupported opcode: {0}".format(opcode))

            total += len(payload)
            if total > MAX_MESSAGE:
                raise ProtocolError("message exceeds MAX_MESSAGE")
            parts.append(payload)

            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def _send_frame(self, opcode, payload):
        """Encode and write a single frame; writes are serialized by the
        send lock."""
        frame = encode_frame(opcode, payload)
        with self._lock:
            self.wfile.write(frame)
            self.wfile.flush()

    def send_text(self, text):
        """Send a text message as a single unfragmented text frame."""
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_json(self, obj):
        """Send a Python object as a JSON-encoded text message."""
        self.send_text(json.dumps(obj))

    def close(self, code=1000):
        """Send a close frame (default: 1000, normal closure) and mark the
        connection closed."""
        self._send_frame(OP_CLOSE, struct.pack("!H", code))
        self.closed = True

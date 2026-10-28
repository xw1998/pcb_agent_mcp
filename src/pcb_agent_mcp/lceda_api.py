"""The WebSocket side of the LCEDA Pro API gateway (ports 49620-49629).

Before this module the server only asked "is anything listening on 49620..49629",
which answers nothing: any process can hold a port. What is implemented here is the
actual protocol - RFC 6455 handshake, client-side masking, fragmentation, ping/pong
and close - so that "the gateway is there" means "it completed a WebSocket
handshake and answered with a frame".

Honesty boundary, stated in every result:

    verified_against_app

is False unless a frame came back from the real application. A handshake served by
this repository's own test server proves the CLIENT, not the gateway: it says the
bytes are RFC-shaped, not that LCEDA speaks them. The gateway also only listens
after the user turns on `外部交互权限` inside the app, so "no listener" is the normal
state and is reported as `no_listener`, never as "the feature is unavailable".

Zero dependencies: sockets, hashlib, base64, struct, os - all stdlib.
"""
import base64
import hashlib
import os
import socket
import struct
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_PORTS = tuple(range(49620, 49630))

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_FRAME = 16 * 1024 * 1024


class WsError(Exception):
    """A protocol-level failure with a reason code the caller can branch on."""

    def __init__(self, message, code="WS_ERROR"):
        Exception.__init__(self, message)
        self.code = code


def accept_key(key):
    """The server's Sec-WebSocket-Accept for a given Sec-WebSocket-Key (RFC 6455 §4.2.2)."""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _mask(payload, key):
    return bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def _build_frame(opcode, payload, mask=True):
    """One client frame. Clients MUST mask (§5.3); servers MUST NOT."""
    head = bytearray()
    head.append(0x80 | opcode)          # FIN + opcode, no RSV bits
    n = len(payload)
    flag = 0x80 if mask else 0x00
    if n < 126:
        head.append(flag | n)
    elif n < 65536:
        head.append(flag | 126)
        head += struct.pack(">H", n)
    else:
        head.append(flag | 127)
        head += struct.pack(">Q", n)
    if mask:
        key = os.urandom(4)
        return bytes(head) + key + _mask(payload, key)
    return bytes(head) + payload


class WsClient(object):
    """A minimal blocking RFC 6455 client: text/binary send, whole-message receive."""

    def __init__(self, host="127.0.0.1", port=49620, path="/", timeout=3.0,
                 origin=None):
        self.host = host
        self.port = int(port)
        self.path = path
        self.timeout = float(timeout)
        self.origin = origin
        self.sock = None
        self.response = {}
        self.sent_bytes = 0
        self.received_bytes = 0

    # ---------------------------------------------------------------- handshake
    def connect(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            sock.settimeout(self.timeout)
            lines = [
                "GET %s HTTP/1.1" % (self.path or "/"),
                "Host: %s:%d" % (self.host, self.port),
                "Upgrade: websocket",
                "Connection: Upgrade",
                "Sec-WebSocket-Key: %s" % key,
                "Sec-WebSocket-Version: 13",
            ]
            if self.origin:
                lines.append("Origin: %s" % self.origin)
            request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
            sock.sendall(request)
            self.sent_bytes += len(request)

            raw = b""
            deadline = time.monotonic() + self.timeout
            while b"\r\n\r\n" not in raw:
                if time.monotonic() > deadline:
                    raise WsError("no HTTP response header within %.1fs" % self.timeout,
                                  "WS_HANDSHAKE_TIMEOUT")
                chunk = sock.recv(4096)
                if not chunk:
                    raise WsError("connection closed during the handshake",
                                  "WS_HANDSHAKE_CLOSED")
                raw += chunk
                self.received_bytes += len(chunk)

            head, _, rest = raw.partition(b"\r\n\r\n")
            text = head.decode("latin-1")
            first, _, header_lines = text.partition("\r\n")
            parts = first.split()
            if len(parts) < 2 or not parts[1].startswith("101"):
                raise WsError("server answered %r, not 101 Switching Protocols"
                              % first.strip(), "WS_NOT_A_GATEWAY")
            headers = {}
            for line in header_lines.split("\r\n"):
                if ":" in line:
                    k, _, v = line.partition(":")
                    headers[k.strip().lower()] = v.strip()
            upgrade = (headers.get("upgrade") or "").lower()
            if upgrade != "websocket":
                raise WsError("101 without Upgrade: websocket", "WS_BAD_HANDSHAKE")
            expected = accept_key(key)
            got = headers.get("sec-websocket-accept")
            if got != expected:
                raise WsError("Sec-WebSocket-Accept mismatch (server did not run "
                              "SHA1(key+GUID))", "WS_BAD_ACCEPT")
            if headers.get("sec-websocket-extensions"):
                raise WsError("server negotiated extensions this client did not offer: %r"
                              % headers["sec-websocket-extensions"],
                              "WS_UNEXPECTED_EXTENSION")
        except Exception:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self.sock = sock
        self.response = headers
        self._pending = rest          # bytes that arrived with the handshake reply
        return headers

    # ------------------------------------------------------------------ sending
    def send_text(self, text):
        return self._send(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, payload):
        return self._send(OP_BINARY, bytes(payload))

    def ping(self, payload=b""):
        return self._send(OP_PING, payload)

    def _send(self, opcode, payload):
        if self.sock is None:
            raise WsError("not connected", "WS_NOT_CONNECTED")
        frame = _build_frame(opcode, payload, mask=True)
        self.sock.sendall(frame)
        self.sent_bytes += len(frame)
        return len(frame)

    # ---------------------------------------------------------------- receiving
    def _read_exact(self, n):
        buf = b""
        if getattr(self, "_pending", b""):
            take = self._pending[:n]
            self._pending = self._pending[n:]
            buf = take
        while len(buf) < n:
            chunk = self.sock.recv(min(65536, n - len(buf)))
            if not chunk:
                raise WsError("connection closed while reading a frame",
                              "WS_CONNECTION_CLOSED")
            self.received_bytes += len(chunk)
            buf += chunk
        return buf

    def _read_frame(self):
        b0, b1 = self._read_exact(2)
        fin = bool(b0 & 0x80)
        if b0 & 0x70:
            raise WsError("RSV bits set without a negotiated extension",
                          "WS_BAD_FRAME")
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read_exact(8))[0]
        if n > MAX_FRAME:
            raise WsError("frame of %d bytes exceeds the %d byte cap" % (n, MAX_FRAME),
                          "WS_FRAME_TOO_LARGE")
        key = self._read_exact(4) if masked else None
        payload = self._read_exact(n) if n else b""
        if key:
            # a server that masks is violating §5.1; unmask so the message is still
            # readable, but say so instead of pretending it is normal
            payload = _mask(payload, key)
        return fin, opcode, payload, masked

    def recv_message(self, max_control=20):
        """Return (opcode, payload) of the next complete data message.

        Control frames are handled in place: a ping is answered with a pong, a close
        is answered and then raised as `WS_CLOSED_BY_PEER`.
        """
        if self.sock is None:
            raise WsError("not connected", "WS_NOT_CONNECTED")
        opcode = None
        pieces = []
        controls = 0
        while True:
            fin, op, payload, masked = self._read_frame()
            if masked:
                self.response["peer_masked_frames"] = True
            if op == OP_PING:
                controls += 1
                if controls > max_control:
                    raise WsError("more than %d control frames in one message"
                                  % max_control, "WS_CONTROL_FLOOD")
                self._send(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
                try:
                    self._send(OP_CLOSE, payload[:2])
                except Exception:  # noqa: BLE001
                    pass
                raise WsError("peer closed the connection (code %r)" % code,
                              "WS_CLOSED_BY_PEER")
            if op == OP_CONT:
                if opcode is None:
                    raise WsError("continuation frame without a first frame",
                                  "WS_BAD_FRAME")
                pieces.append(payload)
            elif op in (OP_TEXT, OP_BINARY):
                if opcode is not None:
                    raise WsError("new data frame before the previous message finished",
                                  "WS_BAD_FRAME")
                opcode = op
                pieces.append(payload)
            else:
                raise WsError("reserved opcode 0x%X" % op, "WS_BAD_FRAME")
            if fin:
                return opcode, b"".join(pieces)

    def recv_json(self):
        import json
        op, payload = self.recv_message()
        if op != OP_TEXT:
            raise WsError("expected a text frame, got opcode 0x%X" % op, "WS_NOT_TEXT")
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise WsError("text frame is not JSON: %s" % exc, "WS_NOT_JSON")

    def close(self, code=1000, timeout=0.5):
        if self.sock is None:
            return
        try:
            old = self.sock.gettimeout()
            self.sock.settimeout(timeout)
            self._send(OP_CLOSE, struct.pack(">H", code))
            try:
                self._read_frame()      # the peer's close echo, best effort
            except Exception:  # noqa: BLE001
                pass
            self.sock.settimeout(old)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                self.sock.close()
            finally:
                self.sock = None


def probe(port, timeout=2.0, path="/"):
    """Full handshake against 127.0.0.1:port. Never raises."""
    started = time.time()
    client = WsClient(port=port, timeout=timeout, path=path)
    out = {"port": int(port), "gateway": False}
    try:
        headers = client.connect()
        out["gateway"] = True
        out["headers"] = headers
    except WsError as exc:
        out["reason_code"] = exc.code
        out["message"] = str(exc)
    except (OSError, socket.error) as exc:
        out["reason_code"] = "WS_CONNECT_FAILED"
        out["message"] = str(exc)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    out["elapsed_ms"] = int((time.time() - started) * 1000)
    # a handshake here was served by whatever holds the port; without a frame from
    # the application itself this number stays False
    out["verified_against_app"] = False
    return out


def probe_ports(ports=None, timeout=2.0):
    """Handshake every candidate port and report per-port facts."""
    ports = list(ports or DEFAULT_PORTS)
    results = [probe(p, timeout=timeout) for p in ports]
    live = [r["port"] for r in results if r["gateway"]]
    if not live:
        verdict = "no_listener"
        why = ("nothing completed a WebSocket handshake on %d..%d. LCEDA Pro only "
               "listens after the user turns on 外部交互权限 inside the app, so this "
               "is the normal state, not a missing feature"
               % (min(ports), max(ports)))
    else:
        verdict = "handshake_ok"
        why = ("%d port(s) completed an RFC 6455 handshake. That proves a WebSocket "
               "server is there; it does not prove it is LCEDA, because no frame was "
               "read from the application" % len(live))
    return {
        "status": "ok",
        "verdict": verdict,
        "why": why,
        "live_ports": live,
        "ports": results,
        "verified_against_app": False,
        "protocol": "RFC 6455 (handshake + client masking + fragmentation + "
                    "ping/pong + close)",
        "tested_by": "tests/test_lceda_api.py drives this client against a stdlib "
                     "test server built into that file - the client is proven, the "
                     "gateway is not",
    }

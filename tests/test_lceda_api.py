"""The LCEDA gateway client: proven against a server that speaks RFC 6455 by the book.

Nothing here contacts LCEDA Pro. A stdlib server thread plays the gateway, and the
tests check the bytes both ways: the client must mask, must accept only a correct
Sec-WebSocket-Accept, must reassemble fragments, must answer ping with pong, and must
refuse to report a gateway when the port is dead.
"""

import socket
import struct
import threading
import unittest

from pcb_agent_mcp import lceda_api


class _Gateway(threading.Thread):
    """A minimal RFC 6455 server: handshake, then scripted replies."""

    def __init__(self, script="echo", accept_override=None, mask_reply=False):
        threading.Thread.__init__(self)
        self.daemon = True
        self.script = script
        self.accept_override = accept_override
        self.mask_reply = mask_reply
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.saw_unmasked = False
        self.request_headers = {}

    # ------------------------------------------------------------------ helpers
    def _frame(self, opcode, payload, fin=True):
        head = bytearray()
        head.append((0x80 if fin else 0x00) | opcode)
        n = len(payload)
        if n < 126:
            head.append(n)
        elif n < 65536:
            head.append(126)
            head += struct.pack(">H", n)
        else:
            head.append(127)
            head += struct.pack(">Q", n)
        return bytes(head) + payload

    def _read_frame(self, conn):
        def exact(n):
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    raise EOFError
                buf += chunk
            return buf
        b0, b1 = exact(2)
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", exact(8))[0]
        key = exact(4) if masked else None
        payload = exact(n) if n else b""
        if key:
            payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        if not masked:
            self.saw_unmasked = True
        return fin, opcode, payload

    def run(self):
        conn, _ = self.srv.accept()
        try:
            raw = b""
            while b"\r\n\r\n" not in raw:
                raw += conn.recv(4096)
            head = raw.split(b"\r\n\r\n")[0].decode("latin-1")
            for line in head.split("\r\n")[1:]:
                k, _, v = line.partition(":")
                self.request_headers[k.strip().lower()] = v.strip()
            key = self.request_headers.get("sec-websocket-key", "")
            accept = self.accept_override or lceda_api.accept_key(key)
            reply = ("HTTP/1.1 101 Switching Protocols\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     "Sec-WebSocket-Accept: %s\r\n\r\n" % accept)
            conn.sendall(reply.encode("ascii"))

            if self.script == "echo":
                while True:
                    fin, op, payload = self._read_frame(conn)
                    if op == 0x8:
                        conn.sendall(self._frame(0x8, payload[:2]))
                        return
                    if op == 0x9:
                        conn.sendall(self._frame(0xA, payload))
                        continue
                    conn.sendall(self._frame(0x1, b"echo:" + payload))
            elif self.script == "fragments":
                conn.sendall(self._frame(0x1, b"he", fin=False))
                conn.sendall(self._frame(0x0, b"ll", fin=False))
                conn.sendall(self._frame(0x0, b"o", fin=True))
                self._read_frame(conn)
            elif self.script == "ping_first":
                conn.sendall(self._frame(0x9, b"hi"))
                fin, op, payload = self._read_frame(conn)   # the pong
                self.pong_seen = (op == 0xA and payload == b"hi")
                conn.sendall(self._frame(0x1, b'{"after":"pong"}'))
            elif self.script == "masked_reply":
                payload = b"masked"
                head = bytes([0x81, 0x80 | len(payload)])
                conn.sendall(head + b"\x01\x02\x03\x04"
                             + bytes(b ^ [1, 2, 3, 4][i % 4]
                                     for i, b in enumerate(payload)))
                self._read_frame(conn)
        except (EOFError, OSError):
            pass
        finally:
            try:
                conn.close()
            finally:
                self.srv.close()


class TheAcceptKeyIsTheSpecFormula(unittest.TestCase):
    def test_rfc_example_vector(self):
        # RFC 6455 §1.3, the worked example
        self.assertEqual(lceda_api.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class TheClientSpeaksTheProtocol(unittest.TestCase):
    def test_it_masks_what_it_sends_and_reads_the_echo(self):
        gw = _Gateway(script="echo")
        gw.start()
        c = lceda_api.WsClient(port=gw.port, timeout=5)
        c.connect()
        c.send_text("ping-from-client")
        op, payload = c.recv_message()
        c.close()
        gw.join(5)
        self.assertEqual(op, lceda_api.OP_TEXT)
        self.assertEqual(payload, b"echo:ping-from-client")
        self.assertFalse(gw.saw_unmasked, "a client frame arrived without the MASK bit")

    def test_it_reassembles_a_fragmented_message(self):
        gw = _Gateway(script="fragments")
        gw.start()
        c = lceda_api.WsClient(port=gw.port, timeout=5)
        c.connect()
        op, payload = c.recv_message()
        c.close()
        gw.join(5)
        self.assertEqual(payload, b"hello")

    def test_it_answers_a_ping_with_a_pong(self):
        gw = _Gateway(script="ping_first")
        gw.start()
        c = lceda_api.WsClient(port=gw.port, timeout=5)
        c.connect()
        seen = c.recv_json()
        c.close()
        gw.join(5)
        self.assertEqual(seen, {"after": "pong"})
        self.assertTrue(getattr(gw, "pong_seen", False))

    def test_a_wrong_accept_is_refused(self):
        gw = _Gateway(script="echo", accept_override="not-the-right-answer")
        gw.start()
        c = lceda_api.WsClient(port=gw.port, timeout=5)
        with self.assertRaises(lceda_api.WsError) as ctx:
            c.connect()
        gw.join(5)
        self.assertEqual(ctx.exception.code, "WS_BAD_ACCEPT")

    def test_a_masked_server_frame_is_unmasked_and_flagged(self):
        gw = _Gateway(script="masked_reply")
        gw.start()
        c = lceda_api.WsClient(port=gw.port, timeout=5)
        c.connect()
        op, payload = c.recv_message()
        c.close()
        gw.join(5)
        self.assertEqual(payload, b"masked")
        self.assertTrue(c.response.get("peer_masked_frames"))

    def test_a_json_reply_is_parsed(self):
        gw = _Gateway(script="echo")
        gw.start()
        c = lceda_api.WsClient(port=gw.port, timeout=5)
        c.connect()
        c.send_text('{"a":1}')
        with self.assertRaises(lceda_api.WsError) as ctx:
            c.recv_json()          # echo: prefix -> not JSON, and it must say so
        c.close()
        gw.join(5)
        self.assertEqual(ctx.exception.code, "WS_NOT_JSON")


class ADeadPortIsNotAGateway(unittest.TestCase):
    def test_probe_reports_no_connect_and_never_claims_verification(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()                              # nothing listens there now
        out = lceda_api.probe(port, timeout=1.0)
        self.assertFalse(out["gateway"])
        self.assertEqual(out["reason_code"], "WS_CONNECT_FAILED")
        self.assertFalse(out["verified_against_app"])

    def test_probe_range_says_handshake_ok_but_not_verified(self):
        gw = _Gateway(script="echo")
        gw.start()
        out = lceda_api.probe_ports([gw.port], timeout=5)
        gw.join(5)
        self.assertEqual(out["verdict"], "handshake_ok")
        self.assertEqual(out["live_ports"], [gw.port])
        self.assertFalse(out["verified_against_app"])

    def test_probe_range_on_nothing_says_no_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        out = lceda_api.probe_ports([port], timeout=1.0)
        self.assertEqual(out["verdict"], "no_listener")
        self.assertIn("外部交互权限", out["why"])


if __name__ == "__main__":
    unittest.main()

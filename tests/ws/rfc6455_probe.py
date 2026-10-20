#!/usr/bin/env python3
"""RFC 6455 protocol probe of a WebSocket endpoint that speaks its own protocol (T-4879).

    rfc6455_probe.py WS_URL [OUT.json]   one connection per case (ws:// or wss://, the subprotocol
                                         from WS_PROTOCOL when set); exit 1 on a failed case
    rfc6455_probe.py --selftest          green against a conforming server, red on each case
                                         against one that ignores the protocol rules

The Autobahn fuzzingclient (crossbario/autobahn-testsuite 25.10.1) needs an echo server: the
broker's view channel sends a `welcome` text frame first and answers text with its own messages,
so Autobahn marks every case FAILED (247 of 247 against antares-broker-jc dev-17, 2026-10-20).
These cases are the Autobahn sections that do not need an echo, numbered as there; the probe
skips the server's data frames (opcode 1 and 2) and judges only control frames and the close.

| case | rule | RFC 6455 |
|---|---|---|
| 2.1 2.3 | a ping is answered by a pong with the same payload | 5.5.2, 5.5.3 |
| 2.5 | a ping over 125 bytes closes 1002 | 5.5 |
| 3.1 | a frame with RSV bits closes 1002 | 5.2 |
| 4.1.1 4.2.1 | a reserved opcode (3, 11) closes 1002 | 5.2 |
| 5.1 | a fragmented ping closes 1002 | 5.4, 5.5 |
| 7.1.1 | a close 1000 is answered by a close 1000 | 5.5.1, 7.4 |
| 7.3.1 | an empty close is answered by a close | 5.5.1 |
| 7.3.2 | a close with a 1-byte payload closes 1002 | 5.5.1 |
| 7.5.1 | a close reason that is not UTF-8 closes 1007 (1002 accepted) | 5.5.1, 8.1 |
| 7.9.1 | a close code 999, 1005 or 1100 closes 1002 | 7.4.1 |
| 10.0 | an unmasked client frame closes 1002 | 5.1 |
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
from urllib.parse import urlsplit

TIMEOUT = 5.0


class Conn:
    def __init__(self, url: str):
        u = urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise ValueError("the URL is ws:// or wss://")
        port = u.port or (443 if u.scheme == "wss" else 80)
        self.sock = socket.create_connection((u.hostname, port), timeout=TIMEOUT)
        if u.scheme == "wss":
            self.sock = ssl.create_default_context().wrap_socket(
                self.sock, server_hostname=u.hostname
            )
        proto = os.getenv("WS_PROTOCOL")
        extra = f"Sec-WebSocket-Protocol: {proto}\r\n" if proto else ""
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{extra}\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed during the handshake")
            head += chunk
        status, _, self.buf = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise ConnectionError(
                f"handshake answered {status.split(b'\r\n', 1)[0].decode()}"
            )

    def send(
        self, opcode: int, payload: bytes = b"", fin=True, rsv=0, mask=True
    ) -> None:
        b0 = (0x80 if fin else 0) | (rsv << 4) | opcode
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", b0, (0x80 if mask else 0) | n)
        elif n < 65536:
            head = struct.pack("!BBH", b0, (0x80 if mask else 0) | 126, n)
        else:
            head = struct.pack("!BBQ", b0, (0x80 if mask else 0) | 127, n)
        if mask:
            key = os.urandom(4)
            payload = bytes(c ^ key[i % 4] for i, c in enumerate(payload))
            head += key
        self.sock.sendall(head + payload)

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def control(self) -> tuple[int, bytes] | None:
        """The next control frame from the server, skipping its data frames; None on EOF/timeout."""
        deadline = time.monotonic() + TIMEOUT
        try:
            while time.monotonic() < deadline:
                b0, b1 = self._read(2)
                n = b1 & 0x7F
                if n == 126:
                    (n,) = struct.unpack("!H", self._read(2))
                elif n == 127:
                    (n,) = struct.unpack("!Q", self._read(8))
                key = self._read(4) if b1 & 0x80 else b""
                payload = self._read(n)
                if key:
                    payload = bytes(c ^ key[i % 4] for i, c in enumerate(payload))
                if b0 & 0x0F >= 8:
                    return b0 & 0x0F, payload
        except (EOFError, OSError):
            return None
        return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def close_code(frame) -> int | None:
    if frame is None or frame[0] != 8:
        return None
    return struct.unpack("!H", frame[1][:2])[0] if len(frame[1]) >= 2 else 1005


def expect_close(codes: tuple[int, ...]):
    def judge(frame) -> str | None:
        got = close_code(frame)
        if frame is None:
            return f"no close frame, wanted close {'/'.join(map(str, codes))}"
        return (
            None if got in codes else f"close {got}, wanted {'/'.join(map(str, codes))}"
        )

    return judge


def expect_pong(payload: bytes):
    def judge(frame) -> str | None:
        if frame is None or frame[0] != 10:
            return f"no pong, got {frame and frame[0]}"
        return None if frame[1] == payload else "pong payload differs from the ping's"

    return judge


def close_payload(code: int, reason: bytes = b"") -> bytes:
    return struct.pack("!H", code) + reason


#: case id -> (frames to send as (opcode, payload, kwargs), the judge of the first control frame)
CASES = {
    "2.1": ([(9, b"", {})], expect_pong(b"")),
    "2.3": ([(9, bytes(range(125)), {})], expect_pong(bytes(range(125)))),
    "2.5": ([(9, b"x" * 126, {})], expect_close((1002,))),
    "3.1": ([(1, b"hi", {"rsv": 1})], expect_close((1002,))),
    "4.1.1": ([(3, b"", {})], expect_close((1002,))),
    "4.2.1": ([(11, b"", {})], expect_close((1002,))),
    "5.1": ([(9, b"a", {"fin": False}), (0, b"b", {})], expect_close((1002,))),
    "7.1.1": ([(8, close_payload(1000), {})], expect_close((1000,))),
    "7.3.1": ([(8, b"", {})], expect_close((1000, 1005))),
    "7.3.2": ([(8, b"\x03", {})], expect_close((1002,))),
    "7.5.1": (
        [
            (
                8,
                close_payload(
                    1000, b"\xce\xba\xe1\xbd\xb9\xcf\x83\xce\xbc\xce\xb5\xed\xa0\x80"
                ),
                {},
            )
        ],
        expect_close((1007, 1002)),
    ),
    "7.9.1": ([(8, close_payload(999), {})], expect_close((1002,))),
    "7.9.2": ([(8, close_payload(1005), {})], expect_close((1002,))),
    "7.9.3": ([(8, close_payload(1100), {})], expect_close((1002,))),
    "10.0": ([(1, b"hi", {"mask": False})], expect_close((1002,))),
}


def probe(url: str) -> dict[str, str]:
    results = {}
    for case, (frames, judge) in CASES.items():
        try:
            conn = Conn(url)
        except (OSError, ConnectionError, ValueError) as e:
            results[case] = f"FAIL: {e}"
            continue
        try:
            for opcode, payload, kw in frames:
                conn.send(opcode, payload, **kw)
            why = judge(conn.control())
        except OSError as e:
            why = f"socket error {e}"
        finally:
            conn.close()
        results[case] = "PASS" if why is None else f"FAIL: {why}"
    return results


def self_test() -> None:
    import asyncio

    import websockets

    async def handler(ws):
        await ws.send('{"welcome": true}')
        async for _ in ws:
            await ws.send('{"error": "not a subscription"}')

    def conforming(ready: threading.Event, port: list[int]):
        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0, max_size=2**16) as srv:
                port.append(srv.sockets[0].getsockname()[1])
                ready.set()
                await asyncio.Future()

        asyncio.run(main())

    def lax(ready: threading.Event, port: list[int]):
        """Answers the handshake and the welcome, then ignores every frame: no pong, no close."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port.append(srv.getsockname()[1])
        ready.set()
        while True:
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: x\r\n\r\n\x81\x02hi"
            )
            threading.Timer(TIMEOUT + 1, c.close).start()

    global TIMEOUT
    TIMEOUT = 1.0
    for server, want_pass in ((conforming, True), (lax, False)):
        ready, port = threading.Event(), []
        threading.Thread(target=server, args=(ready, port), daemon=True).start()
        ready.wait(5)
        results = probe(f"ws://127.0.0.1:{port[0]}/x/views/all/ws")
        failed = {c: r for c, r in results.items() if r != "PASS"}
        if want_pass:
            assert not failed, f"a conforming server failed: {failed}"
        else:
            assert len(failed) == len(CASES), (
                f"a lax server passed: {set(CASES) - set(failed)}"
            )
    print(f"rfc6455_probe selftest: ok ({len(CASES)} cases)")


def main(argv: list[str]) -> int:
    if argv == ["--selftest"]:
        self_test()
        return 0
    if len(argv) not in (1, 2) or not argv[0].startswith(("ws://", "wss://")):
        print(__doc__, file=sys.stderr)
        return 2
    results = probe(argv[0])
    for case, r in results.items():
        print(f"{case:7} {r}")
    failed = sum(r != "PASS" for r in results.values())
    print(f"RFC 6455: {len(results) - failed} of {len(results)} cases pass")
    if len(argv) == 2:
        with open(argv[1], "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

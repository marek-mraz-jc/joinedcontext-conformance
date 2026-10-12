"""A stdlib WebSocket client for the Endpoint's `ngsi-ld-ws` subprotocol (API/07), after
joinedcontext-deployment scripts/smoke-ws.py: the conformance image carries no WebSocket package."""

import base64
import json
import os
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit


def frame(text: str) -> bytes:
    """One masked text frame, as RFC 6455 5.3 requires of a client."""
    payload = text.encode()
    size = len(payload)
    head = bytes([0x81])
    if size < 126:
        head += bytes([0x80 | size])
    elif size < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack("!H", size)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", size)
    mask = os.urandom(4)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _exactly(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("the server closed the connection")
        data += chunk
    return data


def read_message(sock) -> tuple[int, bytes]:
    """The next unfragmented frame: its opcode and payload (a server does not mask)."""
    first, second = _exactly(sock, 2)
    size = second & 0x7F
    if size == 126:
        (size,) = struct.unpack("!H", _exactly(sock, 2))
    elif size == 127:
        (size,) = struct.unpack("!Q", _exactly(sock, 8))
    if size > 1 << 24:
        raise ValueError(f"a frame of {size} bytes")
    mask = _exactly(sock, 4) if second & 0x80 else b""
    payload = _exactly(sock, size)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return first & 0x0F, payload


def connect(url: str, timeout: float, token: str | None = None):
    """The socket and the upgrade's status code; the socket speaks WebSocket only on a 101."""
    parts = urlsplit(url)
    secure = parts.scheme in ("https", "wss")
    sock = socket.create_connection((parts.hostname, parts.port or (443 if secure else 80)), timeout=timeout)
    if secure:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
    auth = f"Authorization: Bearer {token}\r\n" if token else ""
    request = (
        f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
        f"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: ngsi-ld-ws\r\n{auth}\r\n"
    )
    sock.sendall(request.encode())
    head = b""
    while b"\r\n\r\n" not in head and len(head) < 16384:
        chunk = sock.recv(1)
        if not chunk:
            break
        head += chunk
    status = head.split(b"\r\n", 1)[0].split()
    return sock, int(status[1]) if len(status) > 1 and status[1].isdigit() else 0


def probe(url: str, subscription: dict, seconds: float, token: str | None = None) -> tuple[int, int | None, int]:
    """Subscribe once and listen: (upgrade status, x-status of the x-result, x-notifications seen).

    A refused subscription returns at once; an accepted one is listened to for `seconds`, and the
    x-result is waited for up to 30 s even when `seconds` is shorter."""
    try:
        sock, status = connect(url, 30, token)
    except OSError:
        return 0, None, 0
    with sock:
        if status != 101:
            return status, None, 0
        message = {"metadata": {"x-op": "x-subscribe", "x-id": "validate"}, "body": {"type": "Subscription", **subscription}}
        sock.sendall(frame(json.dumps(message)))
        result, notes = None, 0
        start = time.monotonic()
        sock.settimeout(2)
        while time.monotonic() < start + seconds or (result is None and time.monotonic() < start + 30):
            try:
                opcode, payload = read_message(sock)
            except (TimeoutError, socket.timeout):
                continue
            except (OSError, ValueError):
                break
            if opcode == 0x8:
                break
            if opcode != 0x1:
                continue
            meta = json.loads(payload).get("metadata", {})
            if meta.get("x-op") == "x-result" and meta.get("x-id") == "validate":
                result = int(meta.get("x-status", 0))
                if result != 201:
                    break
            elif meta.get("x-op") == "x-notification":
                notes += 1
        return status, result, notes

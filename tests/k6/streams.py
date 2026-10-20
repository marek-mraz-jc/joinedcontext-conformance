#!/usr/bin/env python3
"""The two stream loads k6 cannot hold (T-4889), stdlib asyncio only:

sse   READERS readers of the Portal's live view (GET .../spaces/{space}/live?type=T) held open for
      HOLD seconds: every reader answered 200 with text/event-stream, none dropped before the hold
      ended, p95 time to the answer's headers under --p95-ms.

mqtt  DEVICES MQTT 3.1.1 clients publishing RATE messages a second each (QoS 1) to the edge
      broker for SECONDS: p95 PUBLISH -> PUBACK under --p95-ms, at least 99.9 % acked; with
      --landed-url (an NGSI-LD query that returns the devices' entities in keyValues) at least
      99 % of the devices' entities carry a `sentAt` from this run, which proves the messages went
      through the pipeline into the space, not just into the broker.

    python3 streams.py sse --url https://portal.dev.joinedcontext.com/api/v1/projects/p/spaces/s/live?type=T \\
        --readers 200 --hold 300 --token-env GATEWAY_TOKEN
    python3 streams.py mqtt --host mqtt.dev.joinedcontext.com --port 8883 --tls --devices 500 --rate 1 \\
        --seconds 300 --topic 'load/{device}/attrs' --user-env MQTT_USER --password-env MQTT_PASSWORD \\
        --landed-url 'https://api.dev.joinedcontext.com/api/endpoint/load/ngsi-ld/v1/entities?type=LoadDevice&limit=1000&options=keyValues'

Credentials come from the named environment variables and are never printed. Prints one result
line per check and exits 1 when any check fails. The identities are a viewer (sse) and a device
identity of a scratch space (mqtt): the run writes nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import struct
import sys
import time
import urllib.request
from urllib.parse import urlsplit


def p95(values: list[float]) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def report(checks: list[tuple[str, bool]]) -> int:
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


async def _open(host: str, port: int, tls: bool):
    context = ssl.create_default_context() if tls else None
    return await asyncio.wait_for(
        asyncio.open_connection(
            host, port, ssl=context, server_hostname=host if tls else None
        ),
        15,
    )


async def _reader(url: str, token: str, hold: float, out: dict) -> None:
    u = urlsplit(url)
    tls = u.scheme == "https"
    start = time.monotonic()
    try:
        r, w = await _open(u.hostname, u.port or (443 if tls else 80), tls)
        auth = f"Authorization: Bearer {token}\r\n" if token else ""
        path = u.path + (f"?{u.query}" if u.query else "")
        w.write(
            f"GET {path} HTTP/1.1\r\nHost: {u.netloc}\r\nAccept: text/event-stream\r\n{auth}Connection: keep-alive\r\n\r\n".encode()
        )
        head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 15)
    except (
        TimeoutError,
        OSError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
    ) as err:
        out["failed"].append(type(err).__name__)
        return
    out["connect_ms"].append(1000 * (time.monotonic() - start))
    status = head.split(b" ", 2)[1:2]
    if status != [b"200"] or b"text/event-stream" not in head.lower():
        out["failed"].append(f"status {status[0].decode() if status else '?'}")
        w.close()
        return
    deadline = start + hold
    try:
        while (left := deadline - time.monotonic()) > 0:
            chunk = await asyncio.wait_for(r.read(65536), left)
            if not chunk:
                out["dropped"] += 1
                break
            out["events"] += chunk.count(b"\n\n")
    except TimeoutError:
        pass  # the hold ended with the stream still open: what a reader should see
    except OSError:
        out["dropped"] += 1
    w.close()


async def sse(args) -> int:
    token = os.environ.get(args.token_env, "") if args.token_env else ""
    out = {"connect_ms": [], "failed": [], "dropped": 0, "events": 0}
    await asyncio.gather(
        *(_reader(args.url, token, args.hold, out) for _ in range(args.readers))
    )
    held = args.readers - len(out["failed"]) - out["dropped"]
    print(
        f"sse: {args.readers} readers, {held} held {args.hold}s, {out['events']} events, "
        f"connect p95 {p95(out['connect_ms']):.0f} ms, failures {sorted(set(out['failed']))}"
    )
    return report(
        [
            (
                f"every reader answered 200 event-stream ({len(out['failed'])} did not)",
                not out["failed"],
            ),
            (
                f"no reader dropped before {args.hold}s ({out['dropped']} did)",
                out["dropped"] == 0,
            ),
            (
                f"connect p95 {p95(out['connect_ms']):.0f} ms < {args.p95_ms} ms",
                p95(out["connect_ms"]) < args.p95_ms,
            ),
        ]
    )


def _packet(kind: int, body: bytes) -> bytes:
    """An MQTT fixed header (type<<4 | flags) with its remaining length, then the body."""
    n, length = len(body), b""
    while True:
        byte, n = n % 128, n // 128
        length += bytes([byte | (128 if n else 0)])
        if not n:
            return bytes([kind]) + length + body


def _s(text: str) -> bytes:
    raw = text.encode()
    return struct.pack("!H", len(raw)) + raw


async def _read_packet(r) -> tuple[int, bytes]:
    kind = (await r.readexactly(1))[0]
    mult, length = 1, 0
    while True:
        byte = (await r.readexactly(1))[0]
        length += (byte & 127) * mult
        mult *= 128
        if not byte & 128:
            return kind, await r.readexactly(length)


async def _device(
    i: int, args, user: str, password: str, out: dict, run_start: float
) -> None:
    device = f"{args.prefix}{i:04d}"
    try:
        r, w = await _open(args.host, args.port, args.tls)
        flags = 0x02 | (0x80 if user else 0) | (0x40 if password else 0)
        body = (
            _s("MQTT")
            + bytes([4, flags])
            + struct.pack("!H", 60)
            + _s(f"jc-load-{device}")
        )
        body += (_s(user) if user else b"") + (_s(password) if password else b"")
        w.write(_packet(0x10, body))
        kind, ack = await asyncio.wait_for(_read_packet(r), 15)
        if kind >> 4 != 2 or ack[1] != 0:
            out["refused"].append(f"connack {ack[1] if len(ack) > 1 else '?'}")
            w.close()
            return
    except (TimeoutError, OSError, asyncio.IncompleteReadError) as err:
        out["refused"].append(type(err).__name__)
        return
    sent: dict[int, float] = {}

    async def acks():
        try:
            while True:
                kind, body = await _read_packet(r)
                if kind >> 4 == 4:
                    mid = struct.unpack("!H", body[:2])[0]
                    if mid in sent:
                        out["ack_ms"].append(1000 * (time.monotonic() - sent.pop(mid)))
        except (OSError, asyncio.IncompleteReadError):
            return

    reader = asyncio.create_task(acks())
    topic = args.topic.format(device=device)
    # spread the devices over the first second, so 500 devices are 500 messages a second, not bursts
    await asyncio.sleep((i % 1000) / 1000)
    for seq in range(int(args.seconds * args.rate)):
        mid = seq % 65535 + 1
        payload = json.dumps(
            {"id": device, "seq": seq, "level": seq % 100, "sentAt": run_start}
        ).encode()
        sent[mid] = time.monotonic()
        w.write(_packet(0x32, _s(topic) + struct.pack("!H", mid) + payload))
        out["published"] += 1
        await asyncio.sleep(1 / args.rate)
    await asyncio.sleep(5)  # the last acks
    reader.cancel()
    w.write(_packet(0xE0, b""))
    w.close()


def landed(url: str, token: str, devices: int, prefix: str, since: float) -> int:
    """How many of the run's devices' entities in the space carry this run's sentAt."""
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as answer:
        rows = json.load(answer)
    seen = {
        str(e.get("id", "")).rsplit(":", 1)[-1]
        for e in rows
        if isinstance(e, dict) and e.get("sentAt") == since
    }
    return sum(f"{prefix}{i:04d}" in seen for i in range(devices))


async def mqtt(args) -> int:
    user = os.environ.get(args.user_env, "") if args.user_env else ""
    password = os.environ.get(args.password_env, "") if args.password_env else ""
    out = {"ack_ms": [], "refused": [], "published": 0}
    run_start = round(time.time(), 3)
    await asyncio.gather(
        *(_device(i, args, user, password, out, run_start) for i in range(args.devices))
    )
    acked = len(out["ack_ms"])
    share = acked / out["published"] if out["published"] else 0.0
    print(
        f"mqtt: {args.devices} devices, {out['published']} published, {acked} acked, "
        f"ack p95 {p95(out['ack_ms']):.0f} ms, refused {sorted(set(out['refused']))}"
    )
    checks = [
        (f"every device connected ({len(out['refused'])} refused)", not out["refused"]),
        (f"{share:.2%} acked >= 99.9 %", share >= 0.999),
        (
            f"ack p95 {p95(out['ack_ms']):.0f} ms < {args.p95_ms} ms",
            p95(out["ack_ms"]) < args.p95_ms,
        ),
    ]
    if args.landed_url:
        await asyncio.sleep(args.settle)
        token = os.environ.get(args.token_env, "") if args.token_env else ""
        n = landed(args.landed_url, token, args.devices, args.prefix, run_start)
        checks.append(
            (
                f"{n} of {args.devices} devices landed in the space >= 99 %",
                n >= 0.99 * args.devices,
            )
        )
    return report(checks)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="SSE readers and MQTT devices under load (T-4889)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sse")
    s.add_argument("--url", required=True)
    s.add_argument("--readers", type=int, default=200)
    s.add_argument("--hold", type=float, default=300)
    s.add_argument("--p95-ms", type=float, default=2000)
    s.add_argument("--token-env", default="GATEWAY_TOKEN")
    m = sub.add_parser("mqtt")
    m.add_argument("--host", required=True)
    m.add_argument("--port", type=int, default=8883)
    m.add_argument("--tls", action="store_true")
    m.add_argument("--devices", type=int, default=500)
    m.add_argument("--rate", type=float, default=1)
    m.add_argument("--seconds", type=float, default=300)
    m.add_argument("--topic", default="load/{device}/attrs")
    m.add_argument("--prefix", default="load")
    m.add_argument("--user-env", default="MQTT_USER")
    m.add_argument("--password-env", default="MQTT_PASSWORD")
    m.add_argument("--p95-ms", type=float, default=500)
    m.add_argument("--landed-url")
    m.add_argument("--token-env", default="GATEWAY_TOKEN")
    m.add_argument(
        "--settle",
        type=float,
        default=30,
        help="seconds the pipeline gets before --landed-url is read",
    )
    args = p.parse_args(argv)
    if args.cmd == "sse" and not 1 <= args.readers <= 2000:
        p.error("--readers takes 1..2000")
    if args.cmd == "mqtt" and not (1 <= args.devices <= 5000 and 0 < args.rate <= 10):
        p.error("--devices takes 1..5000 and --rate 0..10")
    return asyncio.run(sse(args) if args.cmd == "sse" else mqtt(args))


if __name__ == "__main__":
    sys.exit(main())

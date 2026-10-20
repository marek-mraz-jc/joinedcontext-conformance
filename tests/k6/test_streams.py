"""streams.py against local stubs (T-4889): a held stream and quick acks pass, a stream that drops
early, a refused reader and a planted slow PUBACK each fail."""

from __future__ import annotations

import asyncio
import threading

import pytest
import streams


def serve(handler) -> int:
    """Run an asyncio server on a free port in a thread; return the port."""
    ready = threading.Event()
    port: list[int] = []

    def run():
        loop = asyncio.new_event_loop()
        server = loop.run_until_complete(asyncio.start_server(handler, "127.0.0.1", 0))
        port.append(server.sockets[0].getsockname()[1])
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    return port[0]


def sse_stub(status: int, close_after: float):
    async def handle(r, w):
        await r.readuntil(b"\r\n\r\n")
        w.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: text/event-stream\r\n\r\n".encode()
        )
        w.write(b"data: {}\n\n")
        await w.drain()
        await asyncio.sleep(close_after)
        w.close()

    return handle


def mqtt_stub(delay: float):
    async def handle(r, w):
        try:
            await streams._read_packet(r)
            w.write(bytes([0x20, 2, 0, 0]))
            while True:
                kind, body = await streams._read_packet(r)
                if kind >> 4 == 3:
                    topic_len = int.from_bytes(body[:2], "big")
                    mid = body[2 + topic_len : 4 + topic_len]
                    await asyncio.sleep(delay)
                    w.write(bytes([0x40, 2]) + mid)
                    await w.drain()
                elif kind >> 4 == 14:
                    break
        except asyncio.IncompleteReadError:
            pass
        w.close()

    return handle


@pytest.mark.parametrize(
    ("status", "close_after", "rc"),
    [(200, 5, 0), (200, 0.2, 1), (403, 5, 1)],
    ids=["held", "dropped-early", "refused"],
)
def test_sse(status, close_after, rc):
    port = serve(sse_stub(status, close_after))
    assert (
        streams.main(
            [
                "sse",
                "--url",
                f"http://127.0.0.1:{port}/live?type=T",
                "--readers",
                "5",
                "--hold",
                "1",
            ]
        )
        == rc
    )


@pytest.mark.parametrize(
    ("delay", "rc"), [(0, 0), (0.3, 1)], ids=["quick", "planted-slowdown"]
)
def test_mqtt(delay, rc, monkeypatch):
    monkeypatch.setattr(streams.asyncio, "sleep", _fast_tail(streams.asyncio.sleep))
    port = serve(mqtt_stub(delay))
    argv = [
        "mqtt",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--devices",
        "3",
        "--rate",
        "5",
        "--seconds",
        "1",
        "--p95-ms",
        "100",
    ]
    assert streams.main(argv) == rc


def _fast_tail(sleep):
    """The 5 s wait for the last acks shortened to 1 s, so the test takes seconds."""

    async def patched(seconds, *a):
        return await sleep(min(seconds, 1), *a)

    return patched


def test_packet_length_encoding():
    assert streams._packet(0x30, b"x" * 200)[:3] == bytes([0x30, 0xC8, 0x01])

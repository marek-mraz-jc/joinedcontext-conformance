"""Fixtures of the WebSocket notification channel suite (T-3518, Antares `surfaces-conformance.md` section 6).

The broker is the `full` build with the `ws` and `ws-views` surfaces, loaded by Antares
`dev/views-rig.sh`; the suite speaks view `all` (`/x/views/all/ws`). Every message the broker
sends is validated against the message schemas of the broker's own `docs/api/asyncapi-ws.yaml`
(`ASYNCAPI_DOC`), the published contract (ADR-0026 rule 11).
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import jsonschema
import pytest
import requests
import yaml
from websockets.asyncio.client import ClientConnection, connect

WAIT = float(os.getenv("WS_WAIT_SECS", "15"))
TYPE = "AirQualityObserved"


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        pytest.skip(f"{name} is not set, see tests/ws/README.md")
    return val.rstrip("/")


def _resolved(node, doc):
    """`node` with every local `$ref` replaced by its target (the document has no cycles)."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target = doc
            for part in ref[2:].split("/"):
                target = target[part]
            return _resolved(target, doc)
        return {k: _resolved(v, doc) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolved(v, doc) for v in node]
    return node


class Contract:
    """The message schemas of the AsyncAPI document, by `x-op`."""

    def __init__(self, path: str) -> None:
        doc = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
        self.messages = {
            f"x-{name}": jsonschema.Draft202012Validator(_resolved(m["payload"], doc))
            for name, m in doc["components"]["messages"].items()
        }

    def check(self, message: dict) -> dict:
        op = message.get("metadata", {}).get("x-op")
        assert op in self.messages, f"no message {op!r} in the AsyncAPI document: {message}"
        errors = [e.message for e in self.messages[op].iter_errors(message)]
        assert not errors, f"{op} breaks asyncapi-ws.yaml: {errors} in {message}"
        return message


@pytest.fixture(scope="session")
def contract() -> Contract:
    return Contract(_require_env("ASYNCAPI_DOC"))


@pytest.fixture(scope="session")
def ws_url() -> str:
    """The channel of view `all`: `ws://.../x/views/all/ws`."""
    return _require_env("WS_URL")


@pytest.fixture(scope="session")
def base(ws_url: str) -> str:
    """The broker's HTTP root, from the channel URL."""
    u = urlsplit(ws_url)
    scheme = "https" if u.scheme == "wss" else "http"
    return f"{scheme}://{u.netloc}"


@pytest.fixture(scope="session")
def ngsild(ws_url: str, base: str) -> str:
    """The NGSI-LD root of the same view: `/x/views/{view}/ngsi-ld/v1`."""
    return base + urlsplit(ws_url).path.removesuffix("/ws") + "/ngsi-ld/v1"


@pytest.fixture(scope="session")
def queue_len() -> int:
    """`ANTARES_WS_QUEUE` of the broker: the length of a connection's resume ring."""
    return int(_require_env("WS_QUEUE"))


@pytest.fixture()
def entity(ngsild: str):
    """A fresh Entity of view `all` for one test, deleted after it."""
    eid = f"urn:ngsi-ld:{TYPE}:ws-{uuid.uuid4().hex[:12]}"
    r = requests.post(
        f"{ngsild}/entities",
        json={"id": eid, "type": TYPE, "temperature": {"type": "Property", "value": 0}},
        timeout=10,
    )
    assert r.status_code == 201, r.text
    yield eid
    requests.delete(f"{ngsild}/entities/{eid}", timeout=10)


def watching(eid: str) -> dict:
    """A Subscription to `temperature` of the Entity `eid`."""
    return {
        "type": "Subscription",
        "entities": [{"id": eid, "type": TYPE}],
        "watchedAttributes": ["temperature"],
        "notification": {"attributes": ["temperature"]},
    }


def set_temperature(ngsild: str, eid: str, value: float) -> None:
    r = requests.patch(
        f"{ngsild}/entities/{eid}/attrs",
        json={"temperature": {"type": "Property", "value": value}},
        timeout=10,
    )
    assert r.status_code == 204, r.text


class Client:
    """One connection: every message it receives is held against the contract."""

    def __init__(self, ws: ClientConnection, contract: Contract) -> None:
        self.ws = ws
        self.contract = contract
        self.connection = ""
        self.token = ""

    async def next(self) -> dict:
        return self.contract.check(json.loads(await asyncio.wait_for(self.ws.recv(), WAIT)))

    async def expect(self, op: str) -> dict:
        m = await self.next()
        assert m["metadata"]["x-op"] == op, m
        return m

    async def send(self, metadata: dict, body: dict | None = None) -> None:
        m = {"metadata": metadata}
        if body is not None:
            m["body"] = body
        await self.ws.send(json.dumps(m))

    async def subscribe(self, body: dict) -> dict:
        await self.send({"x-op": "x-subscribe", "x-id": "s"}, body)
        return await self.expect("x-result")


async def open_client(url: str, contract: Contract) -> Client:
    c = Client(await connect(url, open_timeout=WAIT), contract)
    welcome = await c.expect("x-welcome")
    c.connection = welcome["metadata"]["x-connection"]
    c.token = welcome["metadata"]["x-resume-token"]
    return c


class Receiver:
    """An HTTP notification endpoint on loopback: the bodies the broker POSTs to it."""

    def __init__(self) -> None:
        self.bodies: queue.Queue = queue.Queue()
        bodies = self.bodies

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (http.server's name)
                length = int(self.headers.get("Content-Length", "0"))
                bodies.put(json.loads(self.rfile.read(length)))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_args) -> None:
                pass

        self.server = ThreadingHTTPServer((os.getenv("WS_RECEIVER_HOST", "127.0.0.1"), 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[:2]
        self.uri = f"http://{host}:{port}/notify"

    def next(self) -> dict:
        return self.bodies.get(timeout=WAIT)


@pytest.fixture()
def receiver():
    r = Receiver()
    yield r
    r.server.shutdown()

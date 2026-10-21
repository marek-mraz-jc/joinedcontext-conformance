"""The WebSocket surface of an Endpoint through the context gateway (T-5044, docs API/07, EP-106, EP-107).

`test_ws.py` speaks to the broker's view channel inside Antares CI; these cases speak to a deployed
Endpoint's `wss://api.{domain}/api/endpoint/{slug}/ws`, as a visitor's client does:

| case | API/07 |
|---|---|
| `test_a_plain_get_answers_426_naming_the_upgrade` | §1 |
| `test_a_token_in_the_query_string_is_refused_400` | §1 |
| `test_the_upgrade_answers_101_ngsi_ld_ws_and_a_welcome` | §1, §2 |
| `test_subscribe_update_and_unsubscribe_answer_as_rest_would` | §2 |
| `test_resume_restores_the_connection_and_a_wrong_token_is_404` | §4 |
| `test_an_anonymous_caller_holds_at_most_ten_connections` | §3 (429) |
| `test_a_connection_holds_at_most_64_subscriptions` | §3 (x-status 400) |
| `test_a_write_arrives_as_a_notification` (dev write Endpoint only) | §2 |

A Subscription made over the socket lives on that connection: API/07 §2 says the REST
`/subscriptions` does not list it, so the suite does not look for it there.

    GATEWAY_WS_URL        wss://api.{domain}/api/endpoint/{slug}/ws (anonymous, read-only on prod)
    GATEWAY_WS_TOKEN      a bearer token of the CI service account (dev only, never on prod)
    GATEWAY_WS_WRITE      1 = the Endpoint is a dev write Endpoint: create one entity, expect its
                          notification, delete it (needs the token)
    GATEWAY_WS_WRITE_TYPE the type the write case creates (default AirQualityObserved)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import requests
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

WAIT = float(os.getenv("WS_WAIT_SECS", "15"))
PROTOCOL = "ngsi-ld-ws"
UA = {"User-Agent": "joinedcontext-conformance standards (T-5044)"}


@pytest.fixture(scope="module")
def ws_url() -> str:
    url = os.getenv("GATEWAY_WS_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip(
            "GATEWAY_WS_URL is not set: wss://api.{domain}/api/endpoint/{slug}/ws"
        )
    return url


@pytest.fixture(scope="module")
def http_url(ws_url: str) -> str:
    u = urlsplit(ws_url)
    return urlunsplit(
        ("https" if u.scheme == "wss" else "http", u.netloc, u.path, "", "")
    )


@pytest.fixture(scope="module")
def ngsild(http_url: str) -> str:
    return http_url.removesuffix("/ws") + "/ngsi-ld/v1"


@pytest.fixture(scope="module")
def a_type(ngsild: str) -> str:
    """A type the Endpoint serves, from its own type list."""
    r = requests.get(f"{ngsild}/types", headers=UA, timeout=30)
    assert r.status_code == 200, r.text[:300]
    types = r.json().get("typeList") or []
    if not types:
        pytest.skip("the Endpoint serves no type to subscribe to")
    return types[0]


def run(coro):
    return asyncio.run(coro)


async def opened(url: str, headers: dict | None = None):
    ws = await connect(
        url, subprotocols=[PROTOCOL], additional_headers=headers, open_timeout=WAIT
    )
    welcome = json.loads(await asyncio.wait_for(ws.recv(), WAIT))
    assert welcome["metadata"]["x-op"] == "x-welcome", welcome
    return ws, welcome["metadata"]


async def send(ws, metadata: dict, body: dict | None = None) -> dict:
    """Send one message and answer the x-result echoing its x-id (notifications in between are skipped)."""
    m = {"metadata": metadata} | ({"body": body} if body is not None else {})
    await ws.send(json.dumps(m))
    while True:
        answer = json.loads(await asyncio.wait_for(ws.recv(), WAIT))
        md = answer["metadata"]
        if md["x-op"] == "x-result" and md.get("x-id") == metadata["x-id"]:
            return answer
        assert md["x-op"] in ("x-notification", "x-gap"), answer


def subscription(a_type: str) -> dict:
    return {"type": "Subscription", "entities": [{"type": a_type}]}


def test_a_plain_get_answers_426_naming_the_upgrade(http_url):
    r = requests.get(http_url, headers={**UA, "Accept": "application/json"}, timeout=30)
    assert r.status_code == 426, r.text[:300]
    assert r.headers.get("Upgrade", "").lower() == "websocket", r.headers


def test_a_token_in_the_query_string_is_refused_400(ws_url):
    async def go():
        with pytest.raises(InvalidStatus) as refused:
            await connect(
                f"{ws_url}?access_token=not-a-token",
                subprotocols=[PROTOCOL],
                open_timeout=WAIT,
            )
        return refused.value.response.status_code

    assert run(go()) == 400


def test_the_upgrade_answers_101_ngsi_ld_ws_and_a_welcome(ws_url):
    async def go():
        ws, welcome = await opened(ws_url)
        async with ws:
            assert ws.subprotocol == PROTOCOL
            assert welcome.get("x-connection") and welcome.get("x-resume-token"), (
                welcome
            )

    run(go())


def test_subscribe_update_and_unsubscribe_answer_as_rest_would(ws_url, a_type):
    async def go():
        ws, _ = await opened(ws_url)
        async with ws:
            created = await send(
                ws, {"x-op": "x-subscribe", "x-id": "s1"}, subscription(a_type)
            )
            assert created["metadata"]["x-status"] == 201, created
            sid = created["body"]["id"]
            assert sid.startswith("urn:ngsi-ld:Subscription:"), created
            updated = await send(
                ws,
                {"x-op": "x-update", "x-id": "u1", "x-subscription": sid},
                {"throttling": 5},
            )
            assert updated["metadata"]["x-status"] == 204, updated
            gone = await send(
                ws, {"x-op": "x-unsubscribe", "x-id": "d1", "x-subscription": sid}
            )
            assert gone["metadata"]["x-status"] == 204, gone
            again = await send(
                ws, {"x-op": "x-unsubscribe", "x-id": "d2", "x-subscription": sid}
            )
            assert again["metadata"]["x-status"] == 404, again

    run(go())


def test_resume_restores_the_connection_and_a_wrong_token_is_404(ws_url, a_type):
    async def go():
        ws, first = await opened(ws_url)
        async with ws:
            created = await send(
                ws, {"x-op": "x-subscribe", "x-id": "s1"}, subscription(a_type)
            )
            assert created["metadata"]["x-status"] == 201, created
        resume = {"x-op": "x-resume", "x-connection": first["x-connection"], "x-seq": 0}
        ws, _ = await opened(ws_url)
        async with ws:
            wrong = await send(
                ws, {**resume, "x-id": "r0", "x-resume-token": "not-the-token"}
            )
            assert wrong["metadata"]["x-status"] == 404, wrong
        ws, _ = await opened(ws_url)
        async with ws:
            ok = await send(
                ws, {**resume, "x-id": "r1", "x-resume-token": first["x-resume-token"]}
            )
            # 404 is API/07 §4's answer when the reconnect reached another gateway replica
            assert ok["metadata"]["x-status"] in (200, 404), ok
            if ok["metadata"]["x-status"] == 404:
                pytest.skip(
                    "the reconnect reached another gateway replica (API/07 §4): resume not proven"
                )

    run(go())


def test_an_anonymous_caller_holds_at_most_ten_connections(ws_url):
    if os.getenv("GATEWAY_WS_TOKEN"):
        pytest.skip("counts the anonymous cap: run without GATEWAY_WS_TOKEN")

    async def go():
        held = []
        try:
            for n in range(1, 12):
                try:
                    held.append((await opened(ws_url))[0])
                except InvalidStatus as refused:
                    return n, refused.response.status_code
            return len(held), None
        finally:
            for ws in held:
                with contextlib.suppress(Exception):
                    await ws.close()

    n, status = run(go())
    # another socket of this address may already be open, so the refusal may come sooner
    assert status == 429 and n <= 11, (
        f"connection {n} answered {status}, API/07 §3 caps a caller at 10"
    )


def test_a_connection_holds_at_most_64_subscriptions(ws_url, a_type):
    async def go():
        ws, _ = await opened(ws_url)
        async with ws:
            for n in range(1, 66):
                r = await send(
                    ws, {"x-op": "x-subscribe", "x-id": f"s{n}"}, subscription(a_type)
                )
                if r["metadata"]["x-status"] != 201:
                    return n, r
                await asyncio.sleep(
                    0.06
                )  # under the 20 frames a second API/07 §3 allows
        return 66, None

    n, refused = run(go())
    assert refused is not None, (
        "65 subscriptions on one connection were all accepted (API/07 §3: 64)"
    )
    assert refused["metadata"]["x-status"] == 400, refused
    assert 1 < n <= 65, f"the cap answered at subscription {n}"


def test_a_write_arrives_as_a_notification(ws_url, ngsild):
    token = os.getenv("GATEWAY_WS_TOKEN", "").strip()
    if os.getenv("GATEWAY_WS_WRITE") != "1" or not token:
        pytest.skip(
            "write case: dev write Endpoint only, GATEWAY_WS_WRITE=1 and GATEWAY_WS_TOKEN"
        )
    kind = os.getenv("GATEWAY_WS_WRITE_TYPE", "AirQualityObserved")
    eid = f"urn:ngsi-ld:{kind}:conformance-ws-{uuid.uuid4().hex[:12]}"
    auth = {**UA, "Authorization": f"Bearer {token}"}

    async def go():
        ws, _ = await opened(ws_url, {"Authorization": f"Bearer {token}"})
        async with ws:
            sub = {"type": "Subscription", "entities": [{"id": eid, "type": kind}]}
            created = await send(ws, {"x-op": "x-subscribe", "x-id": "s1"}, sub)
            assert created["metadata"]["x-status"] == 201, created
            r = await asyncio.to_thread(
                requests.post,
                f"{ngsild}/entities",
                json={
                    "id": eid,
                    "type": kind,
                    "temperature": {"type": "Property", "value": 21.5},
                },
                headers=auth,
                timeout=30,
            )
            assert r.status_code == 201, r.text[:300]
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), WAIT))
                if m["metadata"]["x-op"] == "x-notification":
                    return m

    try:
        note = run(go())
    finally:
        requests.delete(f"{ngsild}/entities/{eid}", headers=auth, timeout=30)
    assert note["metadata"]["x-seq"] >= 1, note
    assert [e["id"] for e in note["body"]["data"]] == [eid], note

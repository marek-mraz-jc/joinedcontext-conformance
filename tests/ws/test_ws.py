"""Scripted sessions over the channel of view `all` (T-3518, Antares ADR-0026, surfaces-conformance.md section 6).

Every message the broker sends passes through `Client.next`, which holds it against the AsyncAPI
document; the cases below add what each rule of ADR-0026 says the session does.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

import requests
from conftest import WAIT, Receiver, open_client, set_temperature, watching

DROPPED = {"id", "notifiedAt", "subscriptionId"}


def run(coro):
    return asyncio.run(coro)


def test_welcome_names_the_connection_and_its_resume_token(ws_url, contract):
    async def session():
        c = await open_client(ws_url, contract)
        assert re.fullmatch(r"[a-z2-7]{26}", c.connection)
        assert re.fullmatch(r"[a-z2-7]{52}", c.token)
        await c.ws.close()

    run(session())


def test_subscribe_mints_the_endpoint_and_the_subscription_reads_over_http(ws_url, ngsild, contract, entity):
    async def session():
        c = await open_client(ws_url, contract)
        made = await c.subscribe(watching(entity))
        assert made["metadata"]["x-status"] == 201, made
        assert made["metadata"]["x-id"] == "s"
        sub = made["body"]
        assert sub["notification"]["endpoint"]["uri"] == f"urn:antares:ws:{c.connection}"
        r = requests.get(f"{ngsild}/subscriptions/{sub['id']}", timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["notification"]["endpoint"]["uri"] == sub["notification"]["endpoint"]["uri"]
        await c.ws.close()

    run(session())


def test_a_subscription_naming_another_endpoint_is_refused(ws_url, contract, entity):
    async def session():
        c = await open_client(ws_url, contract)
        body = watching(entity)
        body["notification"]["endpoint"] = {"uri": "http://example.org/notify"}
        refused = await c.subscribe(body)
        assert refused["metadata"]["x-status"] == 400, refused
        assert refused["body"]["type"].endswith("/BadRequestData")
        await c.ws.close()

    run(session())


def test_two_notifications_carry_the_body_the_same_subscription_delivers_over_http(
    ws_url, ngsild, contract, entity, receiver: Receiver
):
    over_http = watching(entity)
    over_http["notification"]["endpoint"] = {"uri": receiver.uri, "accept": "application/json"}
    r = requests.post(f"{ngsild}/subscriptions", json=over_http, timeout=10)
    assert r.status_code == 201, r.text
    http_sub = r.headers["Location"].rsplit("/", 1)[-1]

    async def session():
        c = await open_client(ws_url, contract)
        made = await c.subscribe(watching(entity))
        assert made["metadata"]["x-status"] == 201, made
        for seq, value in [(1, 21.5), (2, 22.5)]:
            set_temperature(ngsild, entity, value)
            n = await c.expect("x-notification")
            assert n["metadata"]["x-seq"] == seq
            assert n["metadata"]["x-subscription"] == made["body"]["id"]
            assert n["metadata"]["Content-Type"] == "application/json"
            assert n["body"]["subscriptionId"] == made["body"]["id"]
            http = await asyncio.to_thread(receiver.next)
            assert {k: v for k, v in n["body"].items() if k not in DROPPED} == {
                k: v for k, v in http.items() if k not in DROPPED
            }
            assert n["body"]["data"][0]["temperature"]["value"] == value
        await c.ws.close()

    try:
        run(session())
    finally:
        requests.delete(f"{ngsild}/subscriptions/{http_sub}", timeout=10)


def test_update_and_unsubscribe_answer_as_http_would(ws_url, contract, entity):
    async def session():
        c = await open_client(ws_url, contract)
        sub = (await c.subscribe(watching(entity)))["body"]["id"]
        await c.send({"x-op": "x-update", "x-id": "u", "x-subscription": sub}, {"description": "renamed"})
        updated = await c.expect("x-result")
        assert updated["metadata"]["x-status"] == 204, updated
        await c.send({"x-op": "x-unsubscribe", "x-id": "d", "x-subscription": sub})
        deleted = await c.expect("x-result")
        assert deleted["metadata"]["x-status"] == 204, deleted
        await c.send({"x-op": "x-unsubscribe", "x-id": "again", "x-subscription": sub})
        gone = await c.expect("x-result")
        assert gone["metadata"]["x-status"] == 404, gone
        await c.ws.close()

    run(session())


def test_resume_delivers_what_followed_the_last_seq(ws_url, ngsild, contract, entity):
    async def session():
        c = await open_client(ws_url, contract)
        await c.subscribe(watching(entity))
        set_temperature(ngsild, entity, 1)
        assert (await c.expect("x-notification"))["metadata"]["x-seq"] == 1
        await c.ws.close()
        await asyncio.sleep(1)
        set_temperature(ngsild, entity, 2)
        set_temperature(ngsild, entity, 3)
        r = await open_client(ws_url, contract)
        await r.send(
            {"x-op": "x-resume", "x-id": "r", "x-connection": c.connection, "x-resume-token": c.token, "x-seq": 1}
        )
        assert (await r.expect("x-result"))["metadata"]["x-status"] == 200
        # the two changes queued while away coalesce into the latest (ADR-0026 rule 6)
        n = await r.expect("x-notification")
        assert n["metadata"]["x-seq"] == 2
        assert n["body"]["data"][0]["temperature"]["value"] == 3
        await r.ws.close()

    run(session())


def test_resume_past_the_ring_starts_with_a_gap(ws_url, ngsild, contract, entity, queue_len):
    sent = queue_len + 2

    async def session():
        c = await open_client(ws_url, contract)
        await c.subscribe(watching(entity))
        for value in range(1, sent + 1):
            set_temperature(ngsild, entity, value)
            assert (await c.expect("x-notification"))["metadata"]["x-seq"] == value
        await c.ws.close()
        await asyncio.sleep(1)
        r = await open_client(ws_url, contract)
        await r.send(
            {"x-op": "x-resume", "x-id": "r", "x-connection": c.connection, "x-resume-token": c.token, "x-seq": 0}
        )
        assert (await r.expect("x-result"))["metadata"]["x-status"] == 200
        gap = await r.expect("x-gap")
        assert (gap["metadata"]["x-from"], gap["metadata"]["x-to"]) == (1, sent - queue_len)
        kept = [(await r.expect("x-notification"))["metadata"]["x-seq"] for _ in range(queue_len)]
        assert kept == list(range(sent - queue_len + 1, sent + 1))
        await r.ws.close()

    run(session())


def test_resume_with_a_wrong_token_is_not_found(ws_url, contract):
    async def session():
        c = await open_client(ws_url, contract)
        await c.ws.close()
        await asyncio.sleep(1)
        r = await open_client(ws_url, contract)
        wrong = "a" * 52 if c.token != "a" * 52 else "b" * 52
        await r.send({"x-op": "x-resume", "x-id": "r", "x-connection": c.connection, "x-resume-token": wrong, "x-seq": 0})
        refused = await r.expect("x-result")
        assert refused["metadata"]["x-status"] == 404, refused
        assert refused["body"]["type"].endswith("/ResourceNotFound")
        await r.ws.close()

    run(session())


def test_a_removed_view_closes_its_connections(ws_url, base, contract):
    view = f"ws-gone-{uuid.uuid4().hex[:8]}"
    r = requests.get(f"{base}/q/views/all", timeout=10)
    assert r.status_code == 200, r.text
    copy = r.json() | {"id": view}
    r = requests.put(f"{base}/q/views/{view}", json=copy, timeout=10)
    assert r.status_code in (201, 204), r.text

    async def session():
        c = await open_client(ws_url.replace("/views/all/", f"/views/{view}/"), contract)
        r = requests.delete(f"{base}/q/views/{view}", timeout=10)
        assert r.status_code == 204, r.text
        # the socket sees the removal on its next heartbeat (30 s)
        closing = contract.check(json.loads(await asyncio.wait_for(c.ws.recv(), 45)))
        assert closing["metadata"]["x-reason"] == "view-removed", closing
        await asyncio.wait_for(c.ws.wait_closed(), WAIT)
        assert c.ws.close_code == 1008

    run(session())


def test_a_connection_ends_at_its_max_lifetime(ws_url, contract, entity):
    async def session():
        c = await open_client(ws_url, contract)
        # a Subscription keeps it from the idle close, so the lifetime ends it
        await c.subscribe(watching(entity))
        while True:
            m = json.loads(await asyncio.wait_for(c.ws.recv(), 600))
            contract.check(m)
            if m["metadata"]["x-op"] == "x-closing":
                break
        assert m["metadata"]["x-reason"] == "lifetime"
        await asyncio.wait_for(c.ws.wait_closed(), WAIT)
        assert c.ws.close_code == 1000

    run(session())


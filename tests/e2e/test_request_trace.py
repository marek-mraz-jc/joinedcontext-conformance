"""The five traced operations hold a policy decision and a tenant end to end (T-3917, ADR-N-041).

`dev/trace-request.py` in joinedcontext-platform proves from the code graphs that create entity,
PATCH attribute, query with `q=`, subscribe and temporal read each pass a policy decision and a
pinned tenant on the way from the gateway to the broker store. This suite proves the same on a
running platform:

- an Endpoint whose Policy grants none of the five operations answers each with `403`, and a
  refused write changes nothing in the store (read back with the granted token);
- a caller of space A reaches nothing of space B through any of the five operations: not on B's
  URL with A's token, and not on A's URL with a URN that carries B's `{orgDomain}:{space}:`
  prefix, which names an entity of A (or none) and never one of B.

Since T-3643 the broker's view decides most of these requests, so a refusal can come from the
broker's engine rather than the gateway's PDP; what the suite holds is the outcome the caller and
the store see, which is the same either way.

Environment (a stage without its variables is skipped, never passed):

- `TRACE_A_URL`, `TRACE_A_TOKEN`: the NGSI-LD base of space A (`https://<host>/cs/<a>/ngsi-ld/v1`
  or an Endpoint's `…/api/endpoint/<slug>/ngsi-ld/v1`) and a token granted all five operations
  on `TRACE_TYPE` there.
- `TRACE_B_URL`, `TRACE_B_TOKEN`: the same for space B, a token of B's own.
- `TRACE_B_PREFIX`: B's URN prefix `{orgDomain}:{space}`, e.g. `example.org:space-b`.
- `TRACE_DENY_URL`, `TRACE_DENY_TOKEN`: an Endpoint over space A whose Policy grants this caller
  none of the five operations.
- `TRACE_TYPE` (default `Building`), `TRACE_RUN` (default the process id).

    pytest tests/e2e/test_request_trace.py
"""

from __future__ import annotations

import os
from typing import Any, Iterator
from urllib.parse import quote

import pytest
import requests

TIMEOUT = 30
RUN = os.environ.get("TRACE_RUN", str(os.getpid()))
TYPE = os.environ.get("TRACE_TYPE", "Building")
LD = "application/ld+json"
CORE = "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"


def door(name: str) -> tuple[str, requests.Session]:
    base = os.environ.get(f"TRACE_{name}_URL")
    if not base:
        pytest.skip(f"TRACE_{name}_URL is not set")
    client = requests.Session()
    token = os.environ.get(f"TRACE_{name}_TOKEN")
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return base.rstrip("/"), client


def b_prefix() -> str:
    prefix = os.environ.get("TRACE_B_PREFIX")
    if not prefix:
        pytest.skip("TRACE_B_PREFIX is not set")
    return prefix


def entity(urn: str, name: str) -> dict[str, Any]:
    return {"id": urn, "type": TYPE, "name": {"type": "Property", "value": name}, "@context": CORE}


def subscription(urn: str) -> dict[str, Any]:
    return {
        "type": "Subscription",
        "entities": [{"id": urn, "type": TYPE}],
        "notification": {"endpoint": {"uri": "http://127.0.0.1:9/never", "accept": "application/json"}},
        "@context": CORE,
    }


def path(urn: str) -> str:
    return quote(urn, safe="")


def name_of(base: str, client: requests.Session, urn: str) -> str | None:
    answer = client.get(f"{base}/entities/{path(urn)}", params={"options": "keyValues"},
                        headers={"Accept": "application/json"}, timeout=TIMEOUT)
    if answer.status_code == 404:
        return None
    assert answer.status_code == 200, f"read {urn} at {base}: {answer.status_code} {answer.text[:300]}"
    return answer.json().get("name")


def create(base: str, client: requests.Session, urn: str) -> requests.Response:
    return client.post(f"{base}/entities", json=entity(f"{urn}-new", "made"),
                       headers={"Content-Type": LD}, timeout=TIMEOUT)


def patch(base: str, client: requests.Session, urn: str) -> requests.Response:
    return client.patch(f"{base}/entities/{path(urn)}/attrs",
                        json={"name": {"type": "Property", "value": "changed"}},
                        headers={"Content-Type": "application/json"}, timeout=TIMEOUT)


def query(base: str, client: requests.Session, urn: str) -> requests.Response:
    return client.get(f"{base}/entities", params={"type": TYPE, "id": urn, "q": 'name=="a"|name=="b"',
                                                  "options": "keyValues"},
                      headers={"Accept": "application/json"}, timeout=TIMEOUT)


def subscribe(base: str, client: requests.Session, urn: str) -> requests.Response:
    answer = client.post(f"{base}/subscriptions", json=subscription(urn), headers={"Content-Type": LD},
                         timeout=TIMEOUT)
    location = answer.headers.get("Location", "")
    if answer.status_code == 201 and location:
        client.delete(f"{base}/subscriptions/{path(location.rstrip('/').rsplit('/', 1)[-1])}", timeout=TIMEOUT)
    return answer


def temporal(base: str, client: requests.Session, urn: str) -> requests.Response:
    return client.get(f"{base}/temporal/entities/{path(urn)}", headers={"Accept": "application/json"},
                      timeout=TIMEOUT)


#: The five traced operations, each run once on an entity's URN.
OPERATIONS = {"create entity": create, "PATCH attribute": patch, "query with q=": query,
              "subscribe": subscribe, "temporal read": temporal}


def unchanged(seeded: dict, letter: str) -> None:
    """The store holds what the fixture wrote, and nothing an operation made."""
    urn = seeded[f"in_{letter.lower()}"]
    assert name_of(*seeded[letter], urn) == letter.lower(), f"{urn} changed in space {letter}"
    assert name_of(*seeded[letter], f"{urn}-new") is None, f"{urn}-new appeared in space {letter}"


@pytest.fixture(scope="module")
def seeded() -> Iterator[dict[str, Any]]:
    """One entity in A and one in B, the one in B under B's prefix; both removed afterwards."""
    a, b = door("A"), door("B")
    in_a = f"urn:ngsi-ld:{TYPE}:trace-a-{RUN}"
    in_b = f"urn:ngsi-ld:{TYPE}:{b_prefix()}:trace-b-{RUN}"
    made: list[tuple[tuple[str, requests.Session], str]] = []
    try:
        for (base, client), urn, name in ((a, in_a, "a"), (b, in_b, "b")):
            answer = client.post(f"{base}/entities", json=entity(urn, name),
                                 headers={"Content-Type": LD}, timeout=TIMEOUT)
            assert answer.status_code == 201, f"seed {urn}: {answer.status_code} {answer.text[:300]}"
            made.append(((base, client), urn))
        yield {"A": a, "B": b, "in_a": in_a, "in_b": in_b}
    finally:
        for (base, client), urn in made:
            for gone in (urn, f"{urn}-new"):
                client.delete(f"{base}/entities/{path(gone)}", timeout=TIMEOUT)
        base, client = a
        client.delete(f"{base}/entities/{path(in_b + '-new')}", timeout=TIMEOUT)


@pytest.mark.parametrize("op", OPERATIONS)
def test_a_policy_granting_none_of_the_five_refuses_with_403_and_writes_nothing(seeded: dict, op: str) -> None:
    """The decision comes before the store: a 403 that wrote anyway is the defect."""
    answer = OPERATIONS[op](*door("DENY"), seeded["in_a"])
    assert answer.status_code == 403, f"{op}: {answer.status_code} {answer.text[:300]}"
    unchanged(seeded, "A")


@pytest.mark.parametrize("op", OPERATIONS)
def test_a_token_of_space_a_reaches_nothing_of_space_b_on_bs_url(seeded: dict, op: str) -> None:
    base, _ = seeded["B"]
    client = requests.Session()
    client.headers.update({k: v for k, v in seeded["A"][1].headers.items() if k == "Authorization"})
    answer = OPERATIONS[op](base, client, seeded["in_b"])
    # A query may answer with nothing; any other success is the door open.
    leaked = answer.ok and not (op == "query with q=" and answer.text.strip() == "[]")
    assert not leaked, f"{op}: space B answered a token of space A: {answer.status_code} {answer.text[:300]}"
    unchanged(seeded, "B")


@pytest.mark.parametrize("op", OPERATIONS)
def test_a_urn_with_bs_prefix_on_as_url_names_nothing_of_b(seeded: dict, op: str) -> None:
    """ADR-N-041: the prefix is part of the id, never a route; A's URL serves A alone. A create
    under B's prefix through A is A's entity, a subscription through A watches A."""
    answer = OPERATIONS[op](*seeded["A"], seeded["in_b"])
    if op in ("PATCH attribute", "temporal read"):
        assert answer.status_code == 404, f"{op} of B's entity through A: {answer.status_code}"
    elif op == "query with q=":
        assert answer.status_code == 200 and answer.json() == [], f"A found B's entity: {answer.text[:300]}"
    else:
        assert answer.status_code < 500, f"{op}: {answer.status_code} {answer.text[:300]}"
    unchanged(seeded, "B")

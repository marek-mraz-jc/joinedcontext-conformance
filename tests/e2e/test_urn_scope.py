"""The same URN in two spaces of two projects is two entities (T-3086, ADR-N-041, PF-10, PF-42).

An entity is its Context Space and its URN. This suite writes one unprefixed URN into a space of
project A and into a space of project B, and a prefixed URN that names space A into space B, and
then proves, through every door it is given, that each space answers for its own entity only:
read, query, patch, delete, the Endpoint's MCP and its GeoJSON. Every write it makes it removes.

Environment (a stage without its variables is skipped, never passed):

- `URN_SPACE_A_URL`, `URN_SPACE_B_URL`: the NGSI-LD base of the two spaces,
  `https://<host>/cs/<space>/ngsi-ld/v1`, with `URN_SPACE_A_TOKEN` / `URN_SPACE_B_TOKEN` as
  bearer tokens that may create, update and delete `Building` there.
- `URN_ENDPOINT_A_URL`, `URN_ENDPOINT_B_URL`: an Endpoint of each space,
  `https://<host>/api/endpoint/<slug>`, with `URN_ENDPOINT_A_TOKEN` / `URN_ENDPOINT_B_TOKEN`,
  for the MCP and GeoJSON stages.
- `URN_RUN`: a suffix that keeps two runs apart (defaults to the process id).

    jc-conformance e2e        # or: pytest tests/e2e/test_urn_scope.py
"""

from __future__ import annotations

import os
from typing import Any, Iterator
from urllib.parse import quote

import pytest
import requests

TIMEOUT = 30
RUN = os.environ.get("URN_RUN", str(os.getpid()))
#: One URN with no prefix at all, written into both spaces.
SHARED = f"urn:ngsi-ld:Building:shared-{RUN}"
#: A prefixed URN naming space A's organization and space, written into space B only.
NAMED_ELSEWHERE = f"urn:ngsi-ld:Building:example.org:space-a:copied-{RUN}"
LD = "application/ld+json"


def space(letter: str) -> tuple[str, requests.Session]:
    base = os.environ.get(f"URN_SPACE_{letter}_URL")
    if not base:
        pytest.skip(f"URN_SPACE_{letter}_URL names no space")
    client = requests.Session()
    token = os.environ.get(f"URN_SPACE_{letter}_TOKEN")
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return base.rstrip("/"), client


def endpoint(letter: str) -> tuple[str, requests.Session]:
    base = os.environ.get(f"URN_ENDPOINT_{letter}_URL")
    if not base:
        pytest.skip(f"URN_ENDPOINT_{letter}_URL names no Endpoint")
    client = requests.Session()
    token = os.environ.get(f"URN_ENDPOINT_{letter}_TOKEN")
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return base.rstrip("/"), client


def building(urn: str, name: str) -> dict[str, Any]:
    return {
        "id": urn,
        "type": "Building",
        "name": {"type": "Property", "value": name},
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [24.94, 60.17]}},
        "@context": "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
    }


def create(base: str, client: requests.Session, entity: dict[str, Any]) -> None:
    answer = client.post(f"{base}/entities", json=entity, headers={"Content-Type": LD}, timeout=TIMEOUT)
    assert answer.status_code == 201, f"create {entity['id']} in {base}: {answer.status_code} {answer.text[:300]}"


def read(base: str, client: requests.Session, urn: str) -> requests.Response:
    return client.get(
        f"{base}/entities/{quote(urn, safe='')}",
        params={"options": "keyValues"},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
    )


def name_in(base: str, client: requests.Session, urn: str) -> str | None:
    answer = read(base, client, urn)
    if answer.status_code == 404:
        return None
    assert answer.status_code == 200, f"read {urn} in {base}: {answer.status_code}"
    return answer.json().get("name")


def delete(base: str, client: requests.Session, urn: str) -> int:
    return client.delete(f"{base}/entities/{quote(urn, safe='')}", timeout=TIMEOUT).status_code


@pytest.fixture(scope="module")
def spaces() -> Iterator[dict[str, tuple[str, requests.Session]]]:
    """The two spaces, each holding the shared URN under its own name; space B also holds the URN
    that names space A. Everything written is removed afterwards, whatever failed."""
    a, b = space("A"), space("B")
    try:
        create(*a, building(SHARED, "in A"))
        create(*b, building(SHARED, "in B"))
        create(*b, building(NAMED_ELSEWHERE, "written into B"))
        yield {"A": a, "B": b}
    finally:
        for base, client in (a, b):
            for urn in (SHARED, NAMED_ELSEWHERE):
                delete(base, client, urn)


def test_each_space_reads_its_own_entity_under_the_shared_urn(spaces: dict) -> None:
    assert name_in(*spaces["A"], SHARED) == "in A"
    assert name_in(*spaces["B"], SHARED) == "in B"


def test_a_query_by_id_answers_one_entity_per_space(spaces: dict) -> None:
    for letter, expected in (("A", "in A"), ("B", "in B")):
        base, client = spaces[letter]
        answer = client.get(
            f"{base}/entities",
            params={"type": "Building", "id": SHARED, "options": "keyValues"},
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        assert answer.status_code == 200, answer.text[:300]
        found = answer.json()
        assert [entity.get("name") for entity in found] == [expected], f"space {letter}: {found}"


def test_a_urn_naming_another_space_lands_where_it_was_written(spaces: dict) -> None:
    """PF-42: the URN names space A; the write went to space B, and space A holds nothing of it."""
    assert name_in(*spaces["B"], NAMED_ELSEWHERE) == "written into B"
    assert name_in(*spaces["A"], NAMED_ELSEWHERE) is None


def test_a_patch_in_one_space_leaves_the_other_unchanged(spaces: dict) -> None:
    base, client = spaces["A"]
    answer = client.patch(
        f"{base}/entities/{quote(SHARED, safe='')}/attrs",
        json={"name": {"type": "Property", "value": "patched in A"}},
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    assert answer.status_code == 204, answer.text[:300]
    assert name_in(*spaces["A"], SHARED) == "patched in A"
    assert name_in(*spaces["B"], SHARED) == "in B"


def test_mcp_get_entity_answers_each_endpoints_own_entity(spaces: dict) -> None:
    for letter, expected in (("A", "patched in A"), ("B", "in B")):
        base, client = endpoint(letter)
        answer = client.post(
            f"{base}/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "get_entity", "arguments": {"id": SHARED}}},
            headers={"Accept": "application/json, text/event-stream"},
            timeout=TIMEOUT,
        )
        assert answer.status_code == 200, answer.text[:300]
        result = answer.json().get("result") or {}
        assert not result.get("isError"), f"endpoint {letter}: {result}"
        assert expected in str(result), f"endpoint {letter} answered another entity: {result}"


def test_each_endpoints_geojson_carries_its_own_entity(spaces: dict) -> None:
    for letter, expected in (("A", "patched in A"), ("B", "in B")):
        base, client = endpoint(letter)
        answer = client.get(
            f"{base}/ngsi-ld/v1/entities",
            params={"type": "Building", "id": SHARED},
            headers={"Accept": "application/geo+json"},
            timeout=TIMEOUT,
        )
        assert answer.status_code == 200, answer.text[:300]
        features = answer.json().get("features") or []
        assert len(features) == 1, f"endpoint {letter}: {features}"
        assert expected in str(features[0]), f"endpoint {letter}: {features[0]}"


def test_a_delete_in_one_space_leaves_the_other(spaces: dict) -> None:
    """Last: the shared URN goes from space A, and space B still has its own."""
    base, client = spaces["A"]
    assert delete(base, client, SHARED) == 204
    assert name_in(*spaces["A"], SHARED) is None
    assert name_in(*spaces["B"], SHARED) == "in B"

"""Tenant isolation on every data surface, one row per surface and verb (T-5557; PF-32, PF-59, R20).

`test_tenant_isolation.py` plays the attack on the Portal's collections and one gateway read
(`GET {endpoint}/entities`); this file plays it on everything else a running platform serves
data through. Each row is one call a token of project A makes on an identifier of project B
(an Endpoint slug, a Context Space, a named MCP server, the project itself) and the same call on
an identifier nobody created. The row holds when the two answers are the same, once the asked
identifier is masked in the body: a refusal that differs from "does not exist" is a directory of
the other project (R20).

Three kinds of row:

- `like-missing`: the answer equals the answer for a name nobody created.
- `absent`: a listing the caller may read (the catalogue, the hub's endpoint list) never names B's
  slug or entity.
- `stream`: a WebSocket or SSE stream on B's identifier is refused like a missing one; should it
  open, no frame may arrive in the 5 s after a write in B.

The gateway takes the tenant only from the path and the verified token
(`context-gateway/src/middleware/tenancy.rs` `strip_client_headers` and `pin_caller`,
`auth/subject.rs` `admit`), so the rows walk the paths. GraphQL has no surface
(`jc-core/tests/endpoint_tests.rs` refuses it); broker-internal routes (`/q`, `/jsonldContexts`)
are not mapped by the gateway and are out of this file.

Every input names its own environment variable and the suite skips without it, so it runs against
the throwaway two-project stub in CI (`selftest_tenancy.py`) and never needs dev:

- `GATEWAY_URL`, `PORTAL_URL`: the two hosts (`https://api.{domain}`, `https://portal.{domain}`).
- `TOKEN_PROJECT_A`: a token of a member of project A only.
- `PROJECT_B`, `OTHER_SLUG` (a `project-list` Endpoint of B serving every representation),
  `OTHER_SPACE`, `OTHER_TYPE`, `OTHER_ENTITY` (an entity B holds), `OTHER_MCP_SERVER` (a named
  MCP server of B), `OTHER_DATAMODEL` (a DataModel of B).
- `TOKEN_PROJECT_B` (optional): writes into B while a stream row listens.
"""

from __future__ import annotations

import json
import os
import time
import typing
from dataclasses import dataclass

import pytest
import requests

TIMEOUT = 30
#: How long a stream row listens for a frame after the write in B.
LISTEN_SECONDS = 5
#: An Endpoint slug, a project and a space nobody created: 26 base32 characters (EP-02), and a
#: DNS label.
MISSING_SLUG = "nobodyhasthisslugqqqqqqqqq"
MISSING_NAME = "t5557-nobody-has-this"

INPUTS = (
    "GATEWAY_URL",
    "PORTAL_URL",
    "TOKEN_PROJECT_A",
    "PROJECT_B",
    "OTHER_SLUG",
    "OTHER_SPACE",
    "OTHER_TYPE",
    "OTHER_ENTITY",
    "OTHER_MCP_SERVER",
    "OTHER_DATAMODEL",
)


@dataclass(frozen=True)
class Row:
    """One call: `host` is `gateway` or `portal`; `{slug}`, `{project}`, `{space}`, `{type}`,
    `{entity}`, `{server}` and `{datamodel}` in the path and body are filled with B's names, then
    with names nobody created."""

    surface: str
    verb: str
    method: str
    path: str
    host: str = "gateway"
    body: typing.Any = None
    kind: str = "like-missing"

    @property
    def id(self) -> str:
        return f"{self.surface}-{self.verb}"


def _rpc(method: str, params: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


EP = "/api/endpoint/{slug}"
LD = EP + "/ngsi-ld/v1"
ENTITY = {"id": "urn:ngsi-ld:{type}:t5557-matrix", "type": "{type}", "name": {"type": "Property", "value": "x"}}

ROWS: tuple[Row, ...] = (
    # NGSI-LD current state.
    Row("ngsi-ld", "list", "GET", LD + "/entities?type={type}&limit=1"),
    Row("ngsi-ld", "read", "GET", LD + "/entities/{entity}"),
    Row("ngsi-ld", "write", "POST", LD + "/entities", body=ENTITY),
    Row("ngsi-ld", "update", "PATCH", LD + "/entities/{entity}/attrs", body={"name": {"type": "Property", "value": "y"}}),
    Row("ngsi-ld", "delete", "DELETE", LD + "/entities/{entity}"),
    Row("ngsi-ld", "types", "GET", LD + "/types"),
    Row("ngsi-ld", "attributes", "GET", LD + "/attributes"),
    Row("entity-operations", "query", "POST", LD + "/entityOperations/query", body={"type": "Query", "entities": [{"type": "{type}"}]}),
    Row("entity-operations", "write", "POST", LD + "/entityOperations/upsert", body=[ENTITY]),
    # History.
    Row("temporal", "history", "GET", LD + "/temporal/entities?type={type}&lastN=1"),
    Row("temporal", "read", "GET", LD + "/temporal/entities/{entity}?lastN=1"),
    Row("temporal", "write", "POST", LD + "/temporal/entities", body=ENTITY),
    # Subscriptions and their notifications.
    Row("subscriptions", "list", "GET", LD + "/subscriptions"),
    Row(
        "subscriptions",
        "subscribe",
        "POST",
        LD + "/subscriptions",
        body={
            "type": "Subscription",
            "entities": [{"type": "{type}"}],
            "notification": {"endpoint": {"uri": "https://example.org/t5557", "accept": "application/json"}},
        },
    ),
    # Streams.
    Row("websocket", "stream", "GET", EP + "/ws", kind="stream"),
    Row("live-sse", "stream", "GET", "/api/v1/projects/{project}/spaces/{space}/live", host="portal", kind="stream"),
    # The other representations of the same Endpoint.
    Row("ogc", "list", "GET", EP + "/ogc/features/collections"),
    Row("ogc", "read", "GET", EP + "/ogc/features/collections/{type}/items?limit=1"),
    Row("sta", "list", "GET", EP + "/sta/v1.1/Things"),
    Row("sta", "read", "GET", EP + "/sta/v1.1/Datastreams?$top=1"),
    Row("odata", "list", "GET", EP + "/odata/v4/"),
    Row("odata", "read", "GET", EP + "/odata/v4/{type}?$top=1"),
    Row("ical", "read", "GET", EP + "/ical/v1/events.ics"),
    Row("feed", "read", "GET", EP + "/feed/v1/items.atom"),
    Row("csv", "read", "GET", EP + "/file.csv"),
    # What describes the Endpoint.
    Row("catalog", "record", "GET", EP),
    Row("catalog", "schema", "GET", EP + "/schema/index.json"),
    Row("catalog", "openapi", "GET", EP + "/openapi.json"),
    Row("catalog", "access", "GET", EP + "/access"),
    Row("catalog", "list", "GET", "/catalog.jsonld", kind="absent"),
    # The Context Space surface.
    Row("space", "list", "GET", "/cs/{space}/ngsi-ld/v1/entities?type={type}&limit=1"),
    Row("space", "schema", "GET", "/cs/{space}/schema/index.json"),
    # MCP: the Endpoint's own server, the space's, the hub, a named one, the Portal's.
    Row("mcp-endpoint", "list", "POST", EP + "/mcp", body=_rpc("tools/list", {})),
    Row(
        "mcp-endpoint",
        "read",
        "POST",
        EP + "/mcp",
        body=_rpc("tools/call", {"name": "query_entities", "arguments": {"type": "{type}"}}),
    ),
    Row("mcp-space", "list", "POST", "/cs/{space}/mcp", body=_rpc("tools/list", {})),
    Row("mcp-hub", "list", "GET", "/api/endpoints", kind="absent"),
    Row(
        "mcp-hub",
        "read",
        "POST",
        "/api/mcp",
        body=_rpc("tools/call", {"name": "get_endpoint", "arguments": {"endpoint": "{slug}"}}),
    ),
    Row(
        "mcp-hub",
        "query",
        "POST",
        "/api/mcp",
        body=_rpc("tools/call", {"name": "query_entities", "arguments": {"endpoint": "{slug}", "type": "{type}"}}),
    ),
    Row("mcp-named", "list", "POST", "/api/mcp/{project}/{server}", body=_rpc("tools/list", {})),
    Row(
        "mcp-named",
        "read",
        "POST",
        "/api/mcp/{project}/{server}",
        body=_rpc("tools/call", {"name": "query_entities", "arguments": {"type": "{type}"}}),
    ),
    Row(
        "mcp-portal",
        "read",
        "POST",
        "/api/v1/mcp",
        host="portal",
        body=_rpc("resources/read", {"uri": "jc://{project}/datamodels/{datamodel}/linkml"}),
    ),
    # CKAN publication of the other project.
    Row("ckan", "read", "GET", "/api/v1/projects/{project}/ckan/status", host="portal"),
)


@pytest.fixture(scope="session")
def inputs() -> dict[str, str]:
    missing = [name for name in INPUTS if not os.getenv(name)]
    if missing:
        pytest.skip(f"environment variables not set: {', '.join(missing)}")
    return {name: os.environ[name] for name in INPUTS}


def _names(inputs: dict[str, str], other: bool) -> dict[str, str]:
    """B's names, or names nobody created; the type stays B's, so only the tenant differs."""
    if other:
        return {
            "slug": inputs["OTHER_SLUG"],
            "project": inputs["PROJECT_B"],
            "space": inputs["OTHER_SPACE"],
            "server": inputs["OTHER_MCP_SERVER"],
            "datamodel": inputs["OTHER_DATAMODEL"],
            "type": inputs["OTHER_TYPE"],
            "entity": inputs["OTHER_ENTITY"],
        }
    return {
        "slug": MISSING_SLUG,
        "project": MISSING_NAME,
        "space": MISSING_NAME,
        "server": MISSING_NAME,
        "datamodel": MISSING_NAME,
        "type": inputs["OTHER_TYPE"],
        "entity": inputs["OTHER_ENTITY"],
    }


def _fill(value: typing.Any, names: dict[str, str]) -> typing.Any:
    if isinstance(value, str):
        for key, name in names.items():
            value = value.replace("{" + key + "}", name)
        return value
    if isinstance(value, list):
        return [_fill(item, names) for item in value]
    if isinstance(value, dict):
        return {_fill(key, names): _fill(item, names) for key, item in value.items()}
    return value


def _url(row: Row, inputs: dict[str, str], names: dict[str, str]) -> str:
    base = inputs["GATEWAY_URL"] if row.host == "gateway" else inputs["PORTAL_URL"]
    return base.rstrip("/") + _fill(row.path, names)


def _masked(text: str, names: dict[str, str]) -> str:
    """The body with every tenant name the call carried replaced, so B's answer and the missing
    one compare on what they say and not on the name echoed back."""
    for key in ("slug", "project", "space", "server", "datamodel"):
        text = text.replace(names[key], "{" + key + "}")
    return text


def _answer(row: Row, inputs: dict[str, str], names: dict[str, str]) -> tuple[int, str]:
    response = requests.request(
        row.method,
        _url(row, inputs, names),
        headers={"Authorization": f"Bearer {inputs['TOKEN_PROJECT_A']}", "Accept": "application/json"},
        json=_fill(row.body, names),
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    return response.status_code, _masked(response.text, names)


def _write_in_b(inputs: dict[str, str]) -> None:
    """One write into B with B's own token, so a stream that leaks has something to carry."""
    token = os.getenv("TOKEN_PROJECT_B")
    if not token:
        return
    names = _names(inputs, other=True)
    entity = _fill({**ENTITY, "id": f"urn:ngsi-ld:{{type}}:t5557-{int(time.time())}"}, names)
    requests.post(
        _url(Row("ngsi-ld", "write", "POST", LD + "/entities"), inputs, names),
        headers={"Authorization": f"Bearer {token}"},
        json=entity,
        timeout=TIMEOUT,
    )


def _websocket(url: str, token: str, inputs: dict[str, str]) -> tuple[int, str, list[str]]:
    """The handshake's answer and, when it opens, every frame of the next 5 s after a write in B."""
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    ws_url = "ws" + url.removeprefix("http")
    try:
        with connect(ws_url, additional_headers={"Authorization": f"Bearer {token}"}, open_timeout=TIMEOUT) as ws:
            _write_in_b(inputs)
            frames: list[str] = []
            deadline = time.monotonic() + LISTEN_SECONDS
            while (left := deadline - time.monotonic()) > 0:
                try:
                    frame = ws.recv(timeout=left)
                except TimeoutError:
                    break
                except Exception:  # noqa: BLE001 - a closed stream carries nothing more
                    break
                frames.append(frame if isinstance(frame, str) else frame.decode(errors="replace"))
            return 101, "", frames
    except InvalidStatus as refused:
        return refused.response.status_code, refused.response.body.decode(errors="replace"), []


def _sse(url: str, token: str, inputs: dict[str, str]) -> tuple[int, str, list[str]]:
    """The stream's status and body and, when it opens, every `data:` line of the next 5 s."""
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        stream=True,
        timeout=(TIMEOUT, LISTEN_SECONDS),
    )
    with response:
        if response.status_code != 200:
            return response.status_code, response.text, []
        _write_in_b(inputs)
        frames: list[str] = []
        deadline = time.monotonic() + LISTEN_SECONDS
        try:
            for line in response.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    frames.append(line)
                if time.monotonic() > deadline:
                    break
        except requests.exceptions.ConnectionError:
            pass
        return 200, "", frames


def _stream(row: Row, inputs: dict[str, str], names: dict[str, str]) -> tuple[int, str, list[str]]:
    url = _url(row, inputs, names)
    token = inputs["TOKEN_PROJECT_A"]
    status, body, frames = (_websocket if row.surface == "websocket" else _sse)(url, token, inputs)
    return status, _masked(body, names), frames


@pytest.mark.parametrize("row", ROWS, ids=[row.id for row in ROWS])
def test_surface_verb_of_the_other_project_answers_like_a_missing_one(row: Row, inputs: dict[str, str]) -> None:
    others, missing = _names(inputs, other=True), _names(inputs, other=False)
    if row.kind == "absent":
        status, body = _answer(row, inputs, others)
        assert status < 500, f"{row.id}: {status} {body[:300]}"
        for name in (inputs["OTHER_SLUG"], inputs["OTHER_ENTITY"]):
            assert name not in body, f"{row.id}: the listing names {name} of project B"
        return
    if row.kind == "stream":
        status, body, frames = _stream(row, inputs, others)
        assert not frames, f"{row.id}: {len(frames)} frame(s) of project B reached A: {frames[:2]}"
        missing_status, missing_body, _ = _stream(row, inputs, missing)
        assert (status, body) == (missing_status, missing_body), (
            f"{row.id}: B's stream answers {status} where a missing one answers {missing_status}"
        )
        return
    answer = _answer(row, inputs, others)
    expected = _answer(row, inputs, missing)
    assert answer == expected, (
        f"{row.id}: B's answer {answer[0]} {answer[1][:300]!r} differs from a missing name's "
        f"{expected[0]} {expected[1][:300]!r}"
    )
    assert inputs["OTHER_ENTITY"] not in json.dumps(answer[1]), f"{row.id}: B's entity reached A"


def test_the_matrix_covers_every_surface_with_at_least_thirty_rows() -> None:
    surfaces = {row.surface for row in ROWS}
    assert len(ROWS) >= 30, len(ROWS)
    assert {
        "ngsi-ld",
        "temporal",
        "subscriptions",
        "websocket",
        "ogc",
        "sta",
        "odata",
        "ical",
        "csv",
        "mcp-endpoint",
        "mcp-space",
        "mcp-hub",
        "mcp-named",
        "mcp-portal",
        "live-sse",
        "ckan",
        "catalog",
    } <= surfaces, surfaces
    assert len({row.id for row in ROWS}) == len(ROWS), "two rows share an id"

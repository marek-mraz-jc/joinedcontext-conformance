"""The same read, over REST and over MCP, with one token, answers the same (T-1860).

AG-29, AG-30, EP-26, TS-19 — an argument never widens a read, and it never narrows one
either: for the same request and the same token the entities, the attribute names and the
values an MCP answer carries are what the REST answer carries. The two doors are one
enforcement path (SP-16), and this suite is what proves it from outside.

The request table below is the read matrix of Architecture/07 section 2: every read tool the
server lists and every argument of its published schema has to appear in it, which
`test_the_matrix_is_covered` checks against the live `tools/list` rather than against a copy
of the table that would drift.
"""

from __future__ import annotations

import json
import os
import typing

import pytest
import requests

from conftest import BROKER, McpClient

try:  # the conformance image pins rdflib through pyshacl; a bare checkout may not have it
    import rdflib
except ImportError:  # pragma: no cover - exercised only where the image is not built
    rdflib = None

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class Case(typing.NamedTuple):
    """One read, written once for each door.

    `{id}` in `arguments` or in `path` is filled with an id the query case discovered, so the
    table needs no entity of its own and works against any seeded space.
    """

    name: str
    tool: str
    arguments: dict
    path: str | None
    params: dict
    method: str = "GET"
    body: dict | None = None


#: The read matrix, one row per operation and per argument people actually pair.
CASES: list[Case] = [
    Case("type", "query_entities", {"type": "AirQualityObserved"}, "entities", {"type": "AirQualityObserved"}),
    Case(
        "type+q",
        "query_entities",
        {"type": "AirQualityObserved", "q": "temperature>0"},
        "entities",
        {"type": "AirQualityObserved", "q": "temperature>0"},
    ),
    Case(
        "type+idPattern",
        "query_entities",
        {"type": "AirQualityObserved", "idPattern": "^urn:ngsi-ld:.*$"},
        "entities",
        {"type": "AirQualityObserved", "idPattern": "^urn:ngsi-ld:.*$"},
    ),
    Case(
        "type+attrs",
        "query_entities",
        {"type": "AirQualityObserved", "attrs": ["temperature"]},
        "entities",
        {"type": "AirQualityObserved", "attrs": "temperature"},
    ),
    Case(
        "type+pick+omit",
        "query_entities",
        {"type": "AirQualityObserved", "pick": ["temperature"], "omit": ["scope"]},
        "entities",
        {"type": "AirQualityObserved", "pick": "temperature", "omit": "scope"},
    ),
    Case(
        "type+window",
        "query_entities",
        {"type": "AirQualityObserved", "limit": 2, "cursor": 0, "count": True},
        "entities",
        {"type": "AirQualityObserved", "limit": "2", "offset": "0", "count": "true"},
    ),
    Case(
        "type+representation",
        "query_entities",
        {"type": "AirQualityObserved", "format": "normalized", "options": ["sysAttrs"], "lang": "en"},
        "entities",
        {"type": "AirQualityObserved", "format": "normalized", "options": "sysAttrs", "lang": "en"},
    ),
    Case(
        "geo+attrs",
        "query_entities",
        {
            "type": "AirQualityObserved",
            "georel": "near;maxDistance==200000",
            "geometry": "Point",
            "coordinates": "[19.15,48.73]",
            "attrs": ["temperature"],
        },
        "entities",
        {
            "type": "AirQualityObserved",
            "georel": "near;maxDistance==200000",
            "geometry": "Point",
            "coordinates": "[19.15,48.73]",
            "attrs": "temperature",
        },
    ),
    Case(
        "scope+csf+local",
        "query_entities",
        {"type": "AirQualityObserved", "scopeQ": "/SK", "local": True},
        "entities",
        {"type": "AirQualityObserved", "scopeQ": "/SK", "local": "true"},
    ),
    Case(
        "join",
        "query_entities",
        {"type": "AirQualityObserved", "join": "flat", "joinLevel": 1, "containedBy": []},
        "entities",
        {"type": "AirQualityObserved", "join": "flat", "joinLevel": "1"},
    ),
    Case(
        "datasets",
        "query_entities",
        {"type": "AirQualityObserved", "datasetId": [], "entityMap": False},
        "entities",
        {"type": "AirQualityObserved"},
    ),
    Case(
        "geoproperty",
        "query_entities",
        {"type": "AirQualityObserved", "geometryProperty": "location"},
        "entities",
        {"type": "AirQualityObserved", "geometryProperty": "location"},
    ),
    Case("types", "list_types", {"details": True}, "types", {"details": "true"}),
    Case("attributes", "list_attributes", {"details": True}, "attributes", {"details": "true"}),
    Case("one type", "get_type", {"type": "AirQualityObserved"}, "types/AirQualityObserved", {}),
    Case("one attribute", "get_attribute", {"attrId": "temperature"}, "attributes/temperature", {}),
    Case(
        "one entity",
        "get_entity",
        {"id": "{id}", "attrs": ["temperature"]},
        "entities/{id}",
        {"attrs": "temperature"},
    ),
    Case(
        "batch by ids",
        "batch_query",
        {"ids": ["{id}"], "type": "AirQualityObserved", "csf": "", "geoproperty": "location"},
        "entityOperations/query",
        {},
        "POST",
        {"entities": [{"type": "AirQualityObserved"}]},
    ),
    Case(
        "batch temporal",
        "batch_query_temporal",
        {
            "type": "AirQualityObserved",
            "timerel": "after",
            "timeAt": "2026-01-01T00:00:00Z",
        },
        "temporal/entityOperations/query",
        {"timerel": "after", "timeAt": "2026-01-01T00:00:00Z"},
        "POST",
        {"entities": [{"type": "AirQualityObserved"}], "temporalQ": {"timerel": "after", "timeAt": "2026-01-01T00:00:00Z"}},
    ),
    Case(
        "one history",
        "retrieve_temporal",
        {"id": "{id}", "timerel": "after", "timeAt": "2026-01-01T00:00:00Z"},
        "temporal/entities/{id}",
        {"timerel": "after", "timeAt": "2026-01-01T00:00:00Z"},
    ),
    Case(
        "one subscription",
        "get_subscription",
        {"id": "urn:ngsi-ld:Subscription:joinedcontext.com:none:none"},
        "subscriptions/urn:ngsi-ld:Subscription:joinedcontext.com:none:none",
        {},
    ),
    Case(
        "temporal+aggr",
        "query_temporal",
        {
            "type": "AirQualityObserved",
            "timerel": "after",
            "timeAt": "2026-01-01T00:00:00Z",
            "timeproperty": "observedAt",
            "lastN": 3,
            "aggrMethods": ["avg"],
            "aggrPeriodDuration": "PT1H",
            "endTimeAt": "2026-12-31T00:00:00Z",
        },
        "temporal/entities",
        {
            "type": "AirQualityObserved",
            "timerel": "after",
            "timeAt": "2026-01-01T00:00:00Z",
            "timeproperty": "observedAt",
            "lastN": "3",
            "aggrMethods": "avg",
            "aggrPeriodDuration": "PT1H",
            "endTimeAt": "2026-12-31T00:00:00Z",
        },
    ),
    Case("subscriptions", "list_subscriptions", {"limit": 5}, "subscriptions", {"limit": "5"}),
    Case(
        "schema",
        "describe_schema",
        {"entityType": "AirQualityObserved", "format": "linkml", "version": 1},
        None,
        {},
    ),
    Case("access", "describe_access", {"format": "permissions"}, None, {}),
]

#: The broker's MCP surface (`/x/views/{view}/mcp` of Antares, `mcp-surface.md`): its own tool
#: schemas (a type is a list, a page goes on with an opaque cursor), so its own table. The REST
#: door is the view's NGSI-LD root, the same seeded `AirQualityObserved` Entities behind both.
#: `{cursor}` is the cursor of the first one-Entity page, so its REST twin is `offset=1`.
AQ = ["AirQualityObserved"]
AFTER = {"timerel": "after", "timeAt": "2026-01-01T00:00:00Z"}
BROKER_CASES: list[Case] = [
    Case("type", "query_entities", {"type": AQ}, "entities", {"type": AQ[0]}),
    Case("type+q", "query_entities", {"type": AQ, "q": "temperature>10"}, "entities", {"type": AQ[0], "q": "temperature>10"}),
    Case(
        "type+idPattern",
        "query_entities",
        {"type": AQ, "idPattern": "^urn:ngsi-ld:AirQualityObserved:bb-.*$"},
        "entities",
        {"type": AQ[0], "idPattern": "^urn:ngsi-ld:AirQualityObserved:bb-.*$"},
    ),
    Case("type+attrs", "query_entities", {"type": AQ, "attrs": ["temperature"]}, "entities", {"type": AQ[0], "attrs": "temperature"}),
    Case("type+pick", "query_entities", {"type": AQ, "pick": ["temperature"]}, "entities", {"type": AQ[0], "pick": "temperature"}),
    Case("type+omit", "query_entities", {"type": AQ, "omit": ["location"]}, "entities", {"type": AQ[0], "omit": "location"}),
    Case("type+window", "query_entities", {"type": AQ, "limit": 1, "count": True}, "entities", {"type": AQ[0], "limit": "1", "count": "true"}),
    Case("next page", "query_entities", {"type": AQ, "limit": 1, "cursor": "{cursor}"}, "entities", {"type": AQ[0], "limit": "1", "offset": "1"}),
    Case("type+lang", "query_entities", {"type": AQ, "lang": "en"}, "entities", {"type": AQ[0], "lang": "en"}),
    Case(
        "geo",
        "query_entities",
        {"type": AQ, "georel": "near;maxDistance==20000", "geometry": "Point", "coordinates": [19.15, 48.73], "geoproperty": "location"},
        "entities",
        {"type": AQ[0], "georel": "near;maxDistance==20000", "geometry": "Point", "coordinates": "[19.15,48.73]", "geoproperty": "location"},
    ),
    Case("scope+local", "query_entities", {"type": AQ, "scopeQ": "/SK/BB", "local": True}, "entities", {"type": AQ[0], "scopeQ": "/SK/BB", "local": "true"}),
    Case("by ids", "query_entities", {"id": ["{id}"], "type": AQ}, "entities", {"id": "{id}", "type": AQ[0]}),
    Case("join", "query_entities", {"type": AQ, "join": "flat", "joinLevel": 1}, "entities", {"type": AQ[0], "join": "flat", "joinLevel": "1"}),
    Case("types", "list_types", {}, "types", {}),
    Case("type details", "list_type_details", {}, "types", {"details": "true"}),
    Case("attributes", "list_attributes", {}, "attributes", {}),
    Case("attribute details", "list_attribute_details", {}, "attributes", {"details": "true"}),
    Case("one type", "get_type", {"type": AQ[0]}, f"types/{AQ[0]}", {}),
    Case("one attribute", "get_attribute", {"attrId": "temperature"}, "attributes/temperature", {}),
    Case("one entity", "get_entity", {"id": "{id}", "attrs": ["temperature"]}, "entities/{id}", {"attrs": "temperature"}),
    Case("one entity's attribute", "get_attribute_value", {"id": "{id}", "attrId": "temperature"}, "entities/{id}/attrs/temperature", {}),
    Case(
        "batch",
        "batch_query",
        {"type": AQ, "attrs": ["temperature"]},
        "entityOperations/query",
        {},
        "POST",
        {"type": "Query", "entities": [{"type": AQ[0]}], "attrs": ["temperature"]},
    ),
    Case(
        "batch temporal",
        "batch_query_temporal",
        {"type": AQ, **AFTER},
        "temporal/entityOperations/query",
        {},
        "POST",
        {"type": "Query", "entities": [{"type": AQ[0]}], "temporalQ": AFTER},
    ),
    Case("one history", "retrieve_temporal", {"id": "{id}", **AFTER, "attrs": ["temperature"]}, "temporal/entities/{id}", {**AFTER, "attrs": "temperature"}),
    Case(
        "history lastN",
        "query_temporal",
        {"type": AQ, **AFTER, "timeproperty": "observedAt", "lastN": 3},
        "temporal/entities",
        {"type": AQ[0], **AFTER, "timeproperty": "observedAt", "lastN": "3"},
    ),
    Case(
        "history aggregated",
        "query_temporal",
        {"type": AQ, "timerel": "between", "timeAt": "2026-01-01T00:00:00Z", "endTimeAt": "2026-12-31T00:00:00Z", "aggrMethods": "avg", "aggrPeriodDuration": "PT1H"},
        "temporal/entities",
        {"type": AQ[0], "timerel": "between", "timeAt": "2026-01-01T00:00:00Z", "endTimeAt": "2026-12-31T00:00:00Z", "aggrMethods": "avg", "aggrPeriodDuration": "PT1H", "options": "aggregatedValues"},
    ),
    Case("one subscription", "get_subscription", {"id": "urn:ngsi-ld:Subscription:none"}, "subscriptions/urn:ngsi-ld:Subscription:none", {}),
    Case("subscriptions", "list_subscriptions", {"limit": 5, "count": True}, "subscriptions", {"limit": "5", "count": "true"}),
]

#: `MCP_SURFACE=broker` runs the broker's table; unset is the platform's.
TABLE = BROKER_CASES if BROKER else CASES

#: Tools whose answer is not a list of entities, compared by their own document instead.
DOCUMENT_TOOLS = {"describe_schema", "describe_access"}


def rest_base(endpoint_mcp_url: str) -> str:
    """The NGSI-LD door of the endpoint whose MCP door is under test."""
    return endpoint_mcp_url.rstrip("/").removesuffix("/mcp") + "/ngsi-ld/v1"


def entities_of(payload: typing.Any) -> list[dict]:
    """Every entity in an answer, whichever door and whichever shape it came in."""
    if isinstance(payload, dict):
        for key in ("entities", "results", "items", "features", "structuredContent"):
            if key in payload:
                return entities_of(payload[key])
        if "id" not in payload and len(payload) == 1:
            # a structured result under the tool's own key (`attribute`, `entityTypes`)
            return entities_of(next(iter(payload.values())))
        return [payload] if "id" in payload else []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def fingerprint(payload: typing.Any) -> list[tuple]:
    """What two doors have to agree on: the ids, the attribute names, and the values.

    Sorted, because neither surface promises an order the other does not, and rendered as
    plain data so a difference is readable in the assertion rather than in a debugger.
    """
    printed = []
    for entity in entities_of(payload):
        members = sorted(
            (name, json.dumps(value, sort_keys=True))
            for name, value in entity.items()
            if not name.startswith("jc:")
        )
        # a list document (EntityTypeList, AttributeList) gets a fresh id per answer
        listed = str(entity.get("type", "")).endswith("List")
        printed.append((None if listed else entity.get("id"), tuple(m for m in members if not (listed and m[0] == "id"))))
    return sorted(printed, key=lambda row: str(row[0]))


def refused(frame: dict) -> bool:
    if "error" in frame:
        return True
    result = frame.get("result", {})
    return result.get("isError") is True


def refusal_words(frame: dict) -> str:
    if "error" in frame:
        return str(frame["error"].get("message", ""))
    content = frame.get("result", {}).get("content", [])
    return " ".join(part.get("text", "") for part in content if isinstance(part, dict))


def filled(value: typing.Any, found: dict[str, str]) -> typing.Any:
    """`{id}` and `{cursor}` filled in, wherever the table wrote them."""
    if isinstance(value, str):
        for placeholder, text in found.items():
            value = value.replace(placeholder, text)
        return value
    if isinstance(value, list):
        return [filled(item, found) for item in value]
    if isinstance(value, dict):
        return {key: filled(item, found) for key, item in value.items()}
    return value


def read_over_rest(
    http: requests.Session, base: str, case: Case, token: str | None, found: dict[str, str]
) -> requests.Response:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{base}/{filled(case.path, found)}"
    params = filled(case.params, found)
    if case.method == "POST":
        headers["Content-Type"] = "application/json"
        return http.post(url, json=filled(case.body, found), params=params, headers=headers, timeout=30)
    return http.get(url, params=params, headers=headers, timeout=30)


def discovered(client: McpClient) -> dict[str, str]:
    """An entity id this caller may read, so the by-id cases need none of their own, and the
    cursor of the page after it. Empty where nothing is seeded."""
    if "query_entities" not in set(client.tool_names()):
        return {}
    kind = AQ if BROKER else AQ[0]
    result = client.tool_call("query_entities", {"type": kind, "limit": 1}).get("result", {})
    entities = entities_of(result)
    if not entities:
        return {}
    cursor = (result.get("structuredContent") or {}).get("nextCursor")
    return {"{id}": str(entities[0].get("id", "")), **({"{cursor}": cursor} if cursor else {})}


def wanted_cases() -> list[Case]:
    """The table, or the first few of it where a deployment asks for a smoke.

    The hourly batch runs this against the live `dev` cluster, where five reads is the agreed
    weight (`MCP_PARITY_CASES=5`); CI runs the whole table against a throwaway broker.
    """
    limit = os.getenv("MCP_PARITY_CASES", "").strip()
    if limit.isdigit() and int(limit) > 0:
        return TABLE[: int(limit)]
    return TABLE


def parity_failures(
    client: McpClient, http: requests.Session, base: str, token: str | None
) -> tuple[list[str], int]:
    """Every case the server offers, sent twice. Returns what disagreed and how many ran."""
    listed = set(client.tool_names())
    found = discovered(client)
    failures: list[str] = []
    ran = 0
    for case in wanted_cases():
        if case.tool not in listed:
            continue
        if any(p not in found and p in json.dumps([case.arguments, case.path]) for p in ("{id}", "{cursor}")):
            # Nothing seeded to read by id: the cases that need one cannot be sent, and a
            # case that was not sent is not a case that passed.
            continue
        frame = client.tool_call(case.tool, filled(case.arguments, found))
        if case.path is None or case.tool in DOCUMENT_TOOLS:
            # A document rather than entities: its own parity case is below.
            ran += 1
            continue
        answer = read_over_rest(http, base, case, token, found)
        ran += 1

        rest_refused = answer.status_code >= 400
        if rest_refused != refused(frame):
            failures.append(
                f"{case.name}: REST answered {answer.status_code} and the tool "
                f"{'refused' if refused(frame) else 'served'}: {refusal_words(frame)!r:.200}"
            )
            continue
        if rest_refused:
            problem = answer.json() if answer.content else {}
            detail = str(problem.get("detail") or problem.get("title") or "")
            if BROKER:  # the broker's tool says status and title, never the detail (MC18)
                detail = f"{problem.get('status', '')} {problem.get('title', '')}".strip()
            words = refusal_words(frame)
            if detail and detail not in words:
                failures.append(
                    f"{case.name}: the two doors refuse in different words: "
                    f"{detail!r:.120} / {words!r:.120}"
                )
            continue

        over_rest = fingerprint(answer.json())
        over_mcp = fingerprint(frame.get("result", {}))
        if over_rest != over_mcp:
            failures.append(f"{case.name}: REST {over_rest!r:.400} / MCP {over_mcp!r:.400}")
    return failures, ran


def test_the_matrix_is_covered(endpoint_mcp: McpClient):
    """AG-84 — a row of the read matrix without a parity case fails here.

    The matrix is read from the server's own `tools/list`, which is generated from the one
    shared parameter table, so a new argument arrives here the moment it is published.
    """
    covered_tools = {case.tool for case in TABLE}
    # By name rather than by (tool, name): the arguments are one shared table on the server
    # too, so an argument proved on the tool people send it to is proved (AG-84).
    covered_arguments = {argument for case in TABLE for argument in case.arguments}
    missing = []
    for tool in endpoint_mcp.call("tools/list").get("tools", []):
        name = tool["name"]
        annotations = tool.get("annotations") or {}
        if annotations.get("readOnlyHint") is not True:
            continue
        if name not in covered_tools:
            missing.append(f"tool {name}")
        for argument in (tool.get("inputSchema") or {}).get("properties", {}):
            if argument not in covered_arguments:
                missing.append(f"argument {argument} (on {name})")
    assert not missing, (
        "the read matrix grew and the parity table did not: "
        f"{sorted(missing)}. Add a case to CASES for each."
    )


def test_every_read_answers_the_same_over_both_doors(
    endpoint_mcp: McpClient,
    endpoint_mcp_url: str,
    http_session: requests.Session,
    mcp_token: str | None,
):
    """AG-29, EP-26 — the full grant: one token, two doors, the same entities."""
    failures, ran = parity_failures(
        endpoint_mcp, http_session, rest_base(endpoint_mcp_url), mcp_token
    )
    assert ran >= 3, f"only {ran} of the table's cases were offered by this server"
    assert not failures, "the two doors disagreed:\n" + "\n".join(failures)


def test_a_narrowed_grant_narrows_both_doors_the_same(
    narrowed_mcp_url: str,
    endpoint_mcp_url: str,
    http_session: requests.Session,
    narrowed_token: str | None,
    make_mcp_client: typing.Callable[..., McpClient],
):
    """R9, AG-85 — and the same holds for a grant that sees less.

    Without a second token this runs the same table as the caller above, which still proves
    the parity it asserts; a deployment that sets `MCP_NARROWED_TOKEN` proves it for a grant
    that is actually narrower, which is where a projection argument would show a difference.
    """
    client = make_mcp_client(narrowed_mcp_url, narrowed_token)
    client.initialize()
    if narrowed_mcp_url != endpoint_mcp_url:
        # a second door that sees what the first sees would make the case prove nothing
        wide = make_mcp_client(endpoint_mcp_url, narrowed_token)
        wide.initialize()
        probe = {"type": AQ if BROKER else AQ[0], "limit": 100}
        seen = [fingerprint(c.tool_call("query_entities", probe).get("result", {})) for c in (client, wide)]
        assert seen[0] != seen[1], f"{narrowed_mcp_url} sees what {endpoint_mcp_url} sees"
    failures, ran = parity_failures(
        client, http_session, rest_base(narrowed_mcp_url), narrowed_token
    )
    assert ran >= 3, f"only {ran} of the table's cases were offered by this server"
    assert not failures, "the two doors disagreed under the narrowed grant:\n" + "\n".join(failures)


@pytest.mark.platform_only  # describe_schema is the platform's (EP-52)
def test_a_document_tool_answers_what_its_rest_route_serves(
    endpoint_mcp: McpClient, endpoint_mcp_url: str, http_session: requests.Session, mcp_token: str | None
):
    """EP-46, EP-52 — the rendered formalisms are the language they claim to be, and the
    tool serves the document the schema route serves."""
    if "describe_schema" not in set(endpoint_mcp.tool_names()):
        pytest.fail("the endpoint MCP serves no describe_schema, which EP-52 requires")

    for fmt, parser in (("linkml", "yaml"), ("shacl", "turtle"), ("rdf", "turtle")):
        frame = endpoint_mcp.tool_call("describe_schema", {"format": fmt})
        assert not refused(frame), f"{fmt} was refused: {refusal_words(frame)}"
        # A tool's structured result is an object with the answer under the tool's own key
        # (API/02 "structuredContent is an object"): `schema` for describe_schema.
        body = frame["result"].get("structuredContent", {}).get("schema", {})
        document = body.get("document")
        assert isinstance(document, str) and document.strip(), (
            f"{fmt} came back without a document: {body!r:.200}"
        )
        if parser == "yaml":
            assert yaml is None or isinstance(yaml.safe_load(document), dict), (
                f"the {fmt} document is not YAML:\n{document[:400]}"
            )
        elif rdflib is not None:
            rdflib.Graph().parse(data=document, format="turtle")
        else:
            assert "@prefix" in document, f"the {fmt} document is not Turtle:\n{document[:400]}"


def test_a_cross_space_id_is_the_same_miss_on_both_doors(
    endpoint_mcp: McpClient, endpoint_mcp_url: str, http_session: requests.Session, mcp_token: str | None
):
    """SP-20 — an id of another space reads nothing, and reads nothing the same way."""
    foreign = "urn:ngsi-ld:Device:joinedcontext.com:doprava:probe-1"
    frame = endpoint_mcp.tool_call("get_entity", {"id": foreign})
    headers = {"Accept": "application/json"}
    if mcp_token:
        headers["Authorization"] = f"Bearer {mcp_token}"
    answer = http_session.get(
        f"{rest_base(endpoint_mcp_url)}/entities/{foreign}", headers=headers, timeout=30
    )

    assert answer.status_code >= 400, f"the REST door served another space: {answer.text!r:.200}"
    assert refused(frame) or not entities_of(frame.get("result", {})), (
        f"the tool served another space: {frame!r:.300}"
    )

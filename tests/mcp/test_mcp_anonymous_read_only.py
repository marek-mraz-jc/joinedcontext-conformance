"""A caller without a token lists and calls the read tools alone, on every MCP surface (T-4168).

The gateway offers an anonymous caller `list_endpoints`, `list_types`, `query_entities`,
`get_entity`, `get_attribute_value`, `retrieve_temporal`, `describe_schema` and
`describe_access`, and `search_documents` where knowledge sources are attached (T-4659), whatever an Endpoint's Policy grants the public role, and refuses every
other tool with a JSON-RPC error before the broker sees it. Each surface named by an
environment variable is checked without a token; one that needs a token is skipped.

The write probe sends empty arguments: on a gateway without the fix it fails the broker's
validation instead of creating anything, so the suite never writes to the system under test.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit, urlunsplit

import pytest

#: What a caller without a token may list and call (platform `mcp/tools.rs` ANONYMOUS_TOOLS, main
#: db4d8cb; T-5044 brought the list up to date).
ANONYMOUS_TOOLS = {
    "list_endpoints",
    "get_endpoint",
    "search",
    # The public pages and documents of the attached knowledge sources (EP-133, T-4659).
    "search_documents",
    "list_types",
    "get_type",
    "list_type_details",
    "list_attributes",
    "list_attribute_details",
    "get_attribute",
    "query_entities",
    "summarize",
    # One area across every type of the view (T-5279).
    "area_overview",
    "get_entity",
    "get_attribute_value",
    "query_temporal",
    "retrieve_temporal",
    "describe_schema",
    "describe_access",
}

#: The surfaces a caller without a token may reach: the hub, an Endpoint's MCP, a space's MCP.
SURFACES = ("HUB_MCP_URL", "ENDPOINT_MCP_URL", "MCP_URL")

pytestmark = pytest.mark.platform_only


@pytest.fixture(params=SURFACES)
def anonymous(request, make_mcp_client):
    url = os.getenv(request.param, "").strip().rstrip("/")
    if not url:
        pytest.skip(f"{request.param} is not set, see tests/mcp/README.md")
    client = make_mcp_client(url, None)
    probe = client.post({"jsonrpc": "2.0", "id": 0, "method": "ping"}, raw=True)
    if probe.status_code in (401, 403, 404):
        pytest.skip(
            f"{request.param} is not open to a caller without a token ({probe.status_code})"
        )
    client.initialize()
    return request.param, client


def test_an_anonymous_tool_list_holds_reads_only(anonymous):
    surface, client = anonymous
    names = set(client.tool_names())
    assert names, f"{surface}: an anonymous caller lists no tool at all"
    assert names <= ANONYMOUS_TOOLS, (
        f"{surface} lists {sorted(names - ANONYMOUS_TOOLS)} without a token"
    )


@pytest.mark.parametrize(
    "tool", ["create_subscription", "delete_subscription", "batch_upsert"]
)
def test_an_anonymous_write_is_refused_before_it_reaches_the_broker(anonymous, tool):
    surface, client = anonymous
    arguments = {"endpoint": "any"} if surface == "HUB_MCP_URL" else {}
    frame = client.tool_call(tool, arguments)
    assert "error" in frame, f"{surface}: {tool} without a token answered {frame}"
    assert frame["error"]["code"] == -32602, f"{surface}: {frame}"
    assert (
        "is not offered here: this surface reads only" in frame["error"]["message"]
    ), f"{surface}: {frame}"


def test_an_anonymous_caller_reads_the_access_it_has(anonymous):
    """describe_access answers an anonymous caller its grant: permissions, each naming a resource
    and actions, and no write among them beyond what a subscription needs (T-5044)."""
    surface, client = anonymous
    if "describe_access" not in client.tool_names():
        pytest.skip(f"{surface} lists no describe_access")
    arguments = {"endpoint": "any"} if surface == "HUB_MCP_URL" else {}
    frame = client.tool_call("describe_access", arguments)
    if surface == "HUB_MCP_URL" and "error" in frame:
        pytest.skip(
            "the hub names no Endpoint for describe_access without one it lists"
        )
    assert "result" in frame and not frame["result"].get("isError"), (
        f"{surface}: {frame}"
    )
    text = frame["result"]["content"][0]["text"]
    permissions = json.loads(text)["permissions"]
    assert permissions, (
        f"{surface}: an anonymous caller is granted nothing: {text[:300]}"
    )
    writes = {
        "createEntity",
        "updateEntity",
        "deleteEntity",
        "upsertBatch",
        "createBatch",
        "deleteBatch",
    }
    for p in permissions:
        assert p.get("resource") and p.get("actions"), p
        assert not writes & set(p["actions"]), (
            f"{surface}: anonymous may {sorted(writes & set(p['actions']))}"
        )


def test_the_hub_searches_its_public_documents_with_their_metadata(make_mcp_client):
    """EP-133, AG-119, AG-120 (T-4659): where the hub lists `search_documents`, one question answers
    hits that each name a page and its publisher, the text cut to 1,200 characters; a call out of
    bounds is refused before anything is searched."""
    url = os.getenv("HUB_MCP_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("HUB_MCP_URL is not set, see tests/mcp/README.md")
    client = make_mcp_client(url, None)
    client.initialize()
    if "search_documents" not in client.tool_names():
        pytest.skip(
            "the hub lists no search_documents: no knowledge source is attached"
        )
    frame = client.tool_call("search_documents", {"query": "úradné hodiny", "limit": 3})
    assert "result" in frame, frame
    hits = frame["result"]["structuredContent"]["hits"]
    assert len(hits) <= 3, hits
    for hit in hits:
        assert hit["url"].startswith("https://"), hit
        assert hit["publisher"], hit
        assert len(hit["text"]) <= 1200, hit
        assert {"title", "description", "published", "modified", "language"} <= set(
            hit
        ), hit
    refused = client.tool_call("search_documents", {"query": "x", "limit": 11})
    assert refused.get("error", {}).get("code") == -32602, refused


def test_the_hub_merges_the_translations_of_a_question(make_mcp_client):
    """EP-133 (T-6167): a question with its translations into the content languages answers one
    fused list whose hits each carry a `cite` line, labelled untrusted data; more than three
    translations are refused before anything is searched."""
    url = os.getenv("HUB_MCP_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("HUB_MCP_URL is not set, see tests/mcp/README.md")
    client = make_mcp_client(url, None)
    client.initialize()
    if "search_documents" not in client.tool_names():
        pytest.skip("the hub lists no search_documents: no knowledge source is attached")
    frame = client.tool_call(
        "search_documents",
        {"query": "plavárne", "translations": {"fi": "uimahallit", "en": "swimming halls"}, "limit": 3},
    )
    assert "result" in frame, frame
    result = frame["result"]
    assert result.get("_meta", {}).get("jc/untrusted") is True, result
    hits = result["structuredContent"]["hits"]
    assert len(hits) <= 3, hits
    for hit in hits:
        assert hit["url"] in hit["cite"], hit
    four = {"fi": "a", "sv": "b", "en": "c", "de": "d"}
    refused = client.tool_call("search_documents", {"query": "x", "translations": four})
    assert refused.get("error", {}).get("code") == -32602, refused


@pytest.mark.parametrize("surface", SURFACES)
def test_an_anonymous_call_on_the_old_host_moves_to_the_data_host(
    surface, http_session
):
    """T-4632, ADR-N-052 §2.1: a surface handed out on `api.{domain}` answers an anonymous POST
    on the apex with a 308 to the same path there, so a client configured before the move keeps
    its body and its method."""
    url = os.getenv(surface, "").strip().rstrip("/")
    parts = urlsplit(url)
    if not (parts.hostname or "").startswith("api."):
        pytest.skip(f"{surface} is not on a data host ({url or 'not set'})")
    old = urlunsplit(parts._replace(netloc=parts.netloc.removeprefix("api.")))
    response = http_session.post(
        old + "?probe=1",
        json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
        headers={"Accept": "application/json, text/event-stream"},
        allow_redirects=False,
        timeout=30,
    )
    assert response.status_code == 308, f"{old}: {response.status_code}"
    assert response.headers.get("Location") == url + "?probe=1", (
        f"{old}: {response.headers.get('Location')}"
    )


#: An Endpoint a caller without a token may not read, and its title (T-6026), probed on the hub
#: door `PUBLIC_MCP_URL`: neither its slug, its title nor a count of it reaches the caller.
NONPUBLIC = ("NONPUBLIC_ENDPOINT", "NONPUBLIC_ENDPOINT_TITLE")
#: A slug nobody registered: a non-public one has to answer like it.
UNKNOWN_SLUG = "zzzzzzzzzzzzzzzzzzzzzzzzzz"


@pytest.fixture
def hub_door(make_mcp_client):
    url = os.getenv("PUBLIC_MCP_URL", "").strip().rstrip("/")
    slug, title = (os.getenv(name, "").strip() for name in NONPUBLIC)
    if not (url and slug and title):
        pytest.skip(
            "PUBLIC_MCP_URL, NONPUBLIC_ENDPOINT and NONPUBLIC_ENDPOINT_TITLE, see tests/mcp/README.md"
        )
    client = make_mcp_client(url, None)
    client.initialize()
    return client, slug, title


def _call(client, tool: str, arguments: dict) -> tuple[int, str]:
    """The status and the body of one anonymous `tools/call`, a refusal included."""
    response = client.post(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        raw=True,
    )
    return response.status_code, response.text


def _shape(status: int, body: str, slug: str) -> tuple:
    """What an answer says, the asked slug (and the suggestions a miss lists) set aside."""
    try:
        frame = json.loads(body)
    except ValueError:
        return status, body.replace(slug, "<slug>")
    result = frame.get("result") if isinstance(frame, dict) else None
    if result is None:
        return status, body.replace(slug, "<slug>")
    return (
        status,
        bool(result.get("isError")),
        sorted(result),
        "structuredContent" in result,
    )


def test_a_non_public_slug_answers_like_an_unknown_one(hub_door):
    """T-6026: asked by slug, a non-public Endpoint is the same refusal as one that does not
    exist, so the answer confirms nothing; its title never comes back."""
    client, slug, title = hub_door
    for tool, arguments in (
        ("get_endpoint", {}),
        ("query_entities", {"type": "AirQualityObserved", "count": True, "limit": 1}),
    ):
        asked = [
            _call(client, tool, dict(arguments, endpoint=s))
            for s in (slug, UNKNOWN_SLUG)
        ]
        assert _shape(*asked[0], slug) == _shape(*asked[1], UNKNOWN_SLUG), (tool, asked)
        assert title not in asked[0][1], f"{tool} names `{title}`: {asked[0][1][:300]}"


def test_a_non_public_title_finds_no_endpoint(hub_door):
    """T-6026: asked by its title words, or listed whole, the hub neither names the non-public
    Endpoint nor counts it."""
    client, slug, title = hub_door
    status, found = _call(client, "get_endpoint", {"endpoint": title})
    assert status == 200, (status, found[:300])
    # The list is read to its last page: a page holds only some Endpoints (T-6061).
    listed = [json.dumps(result) for result, _ in walk_endpoints(client, {})]
    for tool, answer in [("get_endpoint", found)] + [("list_endpoints", page) for page in listed]:
        # A miss quotes what was asked; that echo is the caller's own text, not the hub's.
        body = answer.replace(f"`{title}`", "")
        assert slug not in body and title not in body, (
            f"{tool} leaks the non-public Endpoint: {body[:300]}"
        )


def test_search_finds_no_non_public_endpoint(hub_door):
    """T-6026: a search for the non-public Endpoint's title answers public hits alone."""
    client, slug, title = hub_door
    status, body = _call(client, "search", {"query": title})
    assert status == 200, (status, body[:300])
    assert slug not in body and title not in body, (
        f"search leaks the non-public Endpoint: {body[:300]}"
    )


def test_an_unknown_or_internal_slug_on_the_hub_is_a_tool_error_not_a_401(
    make_mcp_client,
):
    """A hub call naming a slug the caller cannot read answers HTTP 200 with one tool error, never
    401: Claude Code reads a 401 on a call as the connector signed out (T-6031, EP-87). Two
    well-formed slugs nobody owns answer the same bytes, and neither is echoed (SP-20)."""
    url = os.getenv("HUB_MCP_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("HUB_MCP_URL is not set, see tests/mcp/README.md")
    client = make_mcp_client(url, None)
    client.initialize()
    bodies = []
    for slug in ("z" * 26, "y" * 26):
        response = client.post(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "query_entities",
                    "arguments": {"endpoint": slug, "type": "X", "limit": 5},
                },
            },
            raw=True,
        )
        assert response.status_code == 200, (
            f"{slug}: HTTP {response.status_code} {response.text[:300]}"
        )
        assert "www-authenticate" not in response.headers, dict(response.headers)
        frame = response.json()
        assert frame.get("result", {}).get("isError") is True, frame
        assert "list_endpoints" in frame["result"]["content"][0]["text"], frame
        assert slug not in response.text, response.text[:300]
        bodies.append(frame["result"])
    assert bodies[0] == bodies[1], bodies


#: What one anonymous `list_endpoints` page without `limit` may hold (T-6061): dev's 50-row
#: default answered 24,396 bytes, the largest answer a public client read after a schema.
DEFAULT_PAGE_ROWS = 20
DEFAULT_PAGE_BYTES = 10_000


def walk_endpoints(client, first: dict) -> list[tuple[dict, int]]:
    """Every `list_endpoints` page from `first` on, each with its body's bytes, following
    nextCursor to the end."""
    pages: list[tuple[dict, int]] = []
    arguments = dict(first)
    while len(pages) < 1000:
        status, body = _call(client, "list_endpoints", arguments)
        assert status == 200, (arguments, status, body[:300])
        frame = json.loads(body)
        result = frame.get("result") or {}
        assert not result.get("isError"), (arguments, body[:300])
        pages.append((result, len(body.encode("utf-8"))))
        cursor = (result.get("structuredContent") or {}).get("nextCursor")
        if cursor is None:
            return pages
        arguments = dict(first, cursor=cursor)
    raise AssertionError("list_endpoints never ended: 1000 pages")


def test_a_default_endpoint_page_is_small_and_its_cursor_reaches_every_endpoint(hub_door):
    """T-6061: without `limit`, each page holds at most 20 rows in under 10,000 bytes; the
    cursor walks every Endpoint the caller may read once, and never a non-public one."""
    client, slug, title = hub_door
    pages = walk_endpoints(client, {})
    slugs = []
    for result, size in pages:
        rows = result["structuredContent"]["endpoints"]
        assert len(rows) <= DEFAULT_PAGE_ROWS, f"{len(rows)} rows on one default page"
        assert size < DEFAULT_PAGE_BYTES, f"{size} bytes for {len(rows)} rows"
        text = result["content"][0]["text"]
        assert slug not in text and title not in text, f"a page names the non-public Endpoint: {text[:300]}"
        slugs.extend(row["slug"] for row in rows)
    total = pages[0][0]["structuredContent"]["total"]
    assert len(slugs) == len(set(slugs)) == total, (total, slugs)
    assert slug not in slugs


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


def test_an_unknown_or_internal_slug_on_the_hub_is_a_tool_error_not_a_401(anonymous):
    """A hub call naming a slug the caller cannot read answers HTTP 200 with one tool error, never
    401: Claude Code reads a 401 on a call as the connector signed out (T-6031, EP-87). Two
    well-formed slugs nobody owns answer the same bytes, and neither is echoed (SP-20)."""
    surface, client = anonymous
    if surface != "HUB_MCP_URL":
        pytest.skip("only the hub routes by `endpoint`")
    bodies = []
    for slug in ("z" * 26, "y" * 26):
        response = client.post(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "query_entities",
                    "arguments": {"endpoint": slug, "type": ["X"], "limit": 5},
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

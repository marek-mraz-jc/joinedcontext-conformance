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

import os

import pytest

#: What a caller without a token may list and call (platform `mcp/tools.rs` ANONYMOUS_TOOLS).
ANONYMOUS_TOOLS = {
    "list_endpoints",
    "list_types",
    "query_entities",
    "get_entity",
    "get_attribute_value",
    "retrieve_temporal",
    "describe_schema",
    "describe_access",
    # The public pages and documents of the attached knowledge sources (EP-133, T-4659).
    "search_documents",
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
        pytest.skip(f"{request.param} is not open to a caller without a token ({probe.status_code})")
    client.initialize()
    return request.param, client


def test_an_anonymous_tool_list_holds_reads_only(anonymous):
    surface, client = anonymous
    names = set(client.tool_names())
    assert names, f"{surface}: an anonymous caller lists no tool at all"
    assert names <= ANONYMOUS_TOOLS, f"{surface} lists {sorted(names - ANONYMOUS_TOOLS)} without a token"


@pytest.mark.parametrize("tool", ["create_subscription", "delete_subscription", "batch_upsert"])
def test_an_anonymous_write_is_refused_before_it_reaches_the_broker(anonymous, tool):
    surface, client = anonymous
    arguments = {"endpoint": "any"} if surface == "HUB_MCP_URL" else {}
    frame = client.tool_call(tool, arguments)
    assert "error" in frame, f"{surface}: {tool} without a token answered {frame}"
    assert frame["error"]["code"] == -32602, f"{surface}: {frame}"
    assert "without a token" in frame["error"]["message"], f"{surface}: {frame}"


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
        pytest.skip("the hub lists no search_documents: no knowledge source is attached")
    frame = client.tool_call("search_documents", {"query": "úradné hodiny", "limit": 3})
    assert "result" in frame, frame
    hits = frame["result"]["structuredContent"]["hits"]
    assert len(hits) <= 3, hits
    for hit in hits:
        assert hit["url"].startswith("https://"), hit
        assert hit["publisher"], hit
        assert len(hit["text"]) <= 1200, hit
        assert {"title", "description", "published", "modified", "language"} <= set(hit), hit
    refused = client.tool_call("search_documents", {"query": "x", "limit": 11})
    assert refused.get("error", {}).get("code") == -32602, refused

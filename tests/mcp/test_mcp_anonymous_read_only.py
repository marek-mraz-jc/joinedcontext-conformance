"""A caller without a token lists and calls the read tools alone, on every MCP surface (T-4168).

The gateway offers an anonymous caller `list_endpoints`, `list_types`, `query_entities`,
`get_entity`, `get_attribute_value`, `retrieve_temporal`, `describe_schema` and
`describe_access`, whatever an Endpoint's Policy grants the public role, and refuses every
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

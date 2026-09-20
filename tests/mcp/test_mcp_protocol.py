"""Model Context Protocol (MCP) Streamable HTTP + JSON-RPC 2.0 protocol conformance (T-0060).

Requirements: SP-14, SP-15, SP-17, SP-19, SP-20, EP-24, EP-25, AG-04, AG-07.
Asserts transport framing, version negotiation, JSON-RPC 2.0 error invariants, tool
annotations, session lifecycles, and RFC 9728 OAuth 2.1 metadata.
"""

from __future__ import annotations

import os
import re
import typing
import pytest
import requests

try:
    from conftest import McpClient
except ImportError:
    from .conftest import McpClient


def test_ag04_initialize_handshake_negotiates_protocol_and_capabilities(uninit_mcp: McpClient):
    """AG-04 — MCP initialize handshake must negotiate protocol version and return server capabilities."""
    res = uninit_mcp.initialize()

    assert "protocolVersion" in res, f"initialize result missing protocolVersion: {res}"
    negotiated = res["protocolVersion"]
    # Offered version is "2026-07-28"; negotiated version must be <= offered version, never newer
    assert negotiated <= "2026-07-28", f"Server negotiated a newer unknown protocol version: {negotiated}"

    assert "serverInfo" in res, f"initialize result missing serverInfo: {res}"
    assert isinstance(res["serverInfo"].get("name"), str) and res["serverInfo"]["name"], (
        f"serverInfo.name missing or empty: {res['serverInfo']}"
    )

    assert "capabilities" in res, f"initialize result missing capabilities: {res}"
    capabilities = res["capabilities"]
    assert "tools" in capabilities, f"capabilities must advertise tools: {capabilities}"


def test_sp14_jsonrpc_response_framing(mcp: McpClient):
    """SP-14 — Every response frame must conform strictly to JSON-RPC 2.0 semantics."""
    req_id = 99881
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "ping",
    }
    resp = mcp.post(payload, raw=True)
    assert resp.status_code == 200, f"ping returned HTTP {resp.status_code}: {resp.text}"

    frame = resp.json()
    assert isinstance(frame, dict), f"Expected JSON-RPC dict frame, got {type(frame).__name__}"
    assert frame.get("jsonrpc") == "2.0", f"jsonrpc version must be '2.0', got {frame.get('jsonrpc')}"
    assert frame.get("id") == req_id, f"Frame id {frame.get('id')} does not match request id {req_id}"
    assert ("result" in frame) ^ ("error" in frame), (
        f"JSON-RPC frame must contain exactly one of 'result' or 'error': {frame}"
    )
    if "error" in frame:
        err = frame["error"]
        assert isinstance(err, dict), f"error must be an object: {err}"
        assert isinstance(err.get("code"), int), f"error code must be integer: {err}"
        assert isinstance(err.get("message"), str), f"error message must be string: {err}"


def test_sp15_tools_list_returns_valid_schemas_and_unique_names(mcp: McpClient):
    """SP-15 / EP-25 — tools/list must return unique tools with valid draft-07 inputSchema objects."""
    res = mcp.call("tools/list")
    assert isinstance(res, dict) and "tools" in res, f"tools/list must return an object with 'tools': {res}"
    tools = res["tools"]
    assert isinstance(tools, list), f"'tools' must be a list: {tools}"
    assert len(tools) > 0, "tools/list must not be empty"

    names = []
    for tool in tools:
        assert isinstance(tool, dict), f"Tool must be a dict: {tool}"
        name = tool.get("name")
        assert isinstance(name, str) and name, f"Tool missing valid name: {tool}"
        names.append(name)

        desc = tool.get("description")
        assert isinstance(desc, str) and desc, f"Tool {name} missing description: {tool}"

        schema = tool.get("inputSchema")
        assert isinstance(schema, dict), f"Tool {name} missing inputSchema dict: {tool}"
        assert schema.get("type") == "object", f"Tool {name} inputSchema type must be 'object', got {schema.get('type')}"

    assert len(names) == len(set(names)), f"Tool names must be unique: {names}"


def test_ag07_tool_behavior_annotations(mcp: McpClient):
    """AG-07 — Query tools must declare readOnlyHint: true; mutating tools must declare destructiveHint."""
    res = mcp.call("tools/list")
    tools = res.get("tools", [])
    assert tools, "tools/list returned no tools to assert annotations on"

    query_prefixes = ("get_", "list_", "query_", "search_", "describe_")
    mutation_prefixes = ("create_", "update_", "delete_", "apply_", "submit_", "rotate_")

    for tool in tools:
        name = tool["name"]
        annotations = tool.get("annotations", {})

        if any(name.startswith(p) for p in query_prefixes):
            assert annotations.get("readOnlyHint") is True, (
                f"Query tool {name} must declare annotations.readOnlyHint: True, got: {annotations}"
            )
            assert not annotations.get("destructiveHint"), (
                f"Query tool {name} cannot declare destructiveHint: True: {annotations}"
            )

        elif any(name.startswith(p) for p in mutation_prefixes):
            assert not annotations.get("readOnlyHint"), (
                f"Mutation tool {name} cannot declare readOnlyHint: True: {annotations}"
            )
            if any(name.startswith(p) for p in ("delete_", "rotate_")):
                assert annotations.get("destructiveHint") is True, (
                    f"Destructive mutation {name} must declare destructiveHint: True: {annotations}"
                )


def test_ep24_resources_list_advertised_format(mcp: McpClient):
    """EP-24 — When resources capability is advertised, resources/list must return uri and name."""
    init_res = mcp.initialize()
    has_resources = init_res.get("capabilities", {}).get("resources")
    if not has_resources:
        pytest.skip("Server does not advertise resources capability")

    res = mcp.call("resources/list")
    assert isinstance(res, dict) and "resources" in res, f"resources/list missing resources list: {res}"
    resources = res["resources"]
    assert isinstance(resources, list), f"resources must be a list: {resources}"
    for resource in resources:
        assert isinstance(resource.get("uri"), str) and resource["uri"], f"Resource missing uri: {resource}"
        assert isinstance(resource.get("name"), str) and resource["name"], f"Resource missing name: {resource}"


def test_sp14_protocol_error_codes(mcp: McpClient):
    """SP-14 — Server must return standard JSON-RPC 2.0 error codes (-32601, -32600, -32700)."""
    # 1. Unknown method -> -32601
    err_unknown = mcp.error("nonexistent_conformance_method_xyz_404")
    assert err_unknown.get("code") == -32601, f"Unknown method must return -32601, got {err_unknown}"

    # 2. Invalid Request / bad jsonrpc -> -32600
    bad_rpc_payload = {"jsonrpc": "1.0", "id": 12345, "method": "ping"}
    resp_bad_rpc = mcp.post(bad_rpc_payload, raw=True)
    try:
        frame_bad_rpc = resp_bad_rpc.json()
        assert frame_bad_rpc.get("error", {}).get("code") == -32600, (
            f"Invalid jsonrpc field must return -32600, got: {frame_bad_rpc}"
        )
    except Exception as exc:
        raise AssertionError(f"Expected JSON-RPC error frame for bad jsonrpc: {exc}, got {resp_bad_rpc.text}") from exc

    # 3. Parse error -> -32700 or HTTP 400
    resp_malformed = mcp.post(b"{\"jsonrpc\": \"2.0\", broken json", raw=True)
    if resp_malformed.status_code == 400:
        pass  # HTTP 400 Bad Request is valid on transport level
    else:
        assert resp_malformed.status_code == 200, f"Malformed body gave HTTP {resp_malformed.status_code}"
        frame_malformed = resp_malformed.json()
        assert frame_malformed.get("error", {}).get("code") == -32700, (
            f"Parse error must return code -32700, got {frame_malformed}"
        )


def test_sp14_notification_has_no_response_body(mcp: McpClient):
    """SP-14 — Notifications (requests without id) must produce no JSON-RPC body (HTTP 200/202/204)."""
    resp = mcp.notify("notifications/cancelled", {"requestId": "conformance-notification-probe"})
    assert resp.status_code in (200, 202, 204), f"Notification returned HTTP {resp.status_code}: {resp.text}"
    body = resp.text.strip()
    assert not body or body == "{}", f"Notification must produce empty body, got: {resp.text!r:.200}"


def test_sp20_session_lifecycle_and_protocol_version_header(uninit_mcp: McpClient):
    """SP-20 — Unknown Mcp-Session-Id answers 404; missing MCP-Protocol-Version answers 400."""
    init_res = uninit_mcp.initialize()
    if not uninit_mcp.session_id:
        pytest.skip("Server is stateless and did not return Mcp-Session-Id")

    # Unknown session ID must answer HTTP 404 to prompt client to re-initialize
    resp_bad_sess = uninit_mcp.post(
        {"jsonrpc": "2.0", "id": 881, "method": "ping"},
        headers={"Mcp-Session-Id": "conformance-nonexistent-session-id-probe"},
        session=False,
        raw=True,
    )
    assert resp_bad_sess.status_code == 404, (
        f"Request with unknown Mcp-Session-Id must return HTTP 404, got {resp_bad_sess.status_code}"
    )

    # Missing MCP-Protocol-Version header after handshake must return HTTP 400
    resp_no_ver = uninit_mcp.post(
        {"jsonrpc": "2.0", "id": 882, "method": "ping"},
        headers={"MCP-Protocol-Version": ""},
        session=True,
        raw=True,
    )
    assert resp_no_ver.status_code == 400, (
        f"Request missing MCP-Protocol-Version header must return HTTP 400, got {resp_no_ver.status_code}"
    )


def test_sp17_private_mcp_requires_rfc9728_auth(http_session: requests.Session, private_mcp_url: str):
    """SP-17 — Private MCP answers 401 with WWW-Authenticate referencing RFC 9728 resource metadata."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "conformance", "version": "1.0"},
        },
    }
    resp = http_session.post(private_mcp_url, json=payload, timeout=30)
    assert resp.status_code == 401, f"Unauthenticated request to private MCP must return 401, got {resp.status_code}"

    www_auth = resp.headers.get("WWW-Authenticate", "")
    assert "Bearer" in www_auth, f"WWW-Authenticate must challenge with Bearer: {www_auth}"

    # Extract resource_metadata URL from WWW-Authenticate or Link header per RFC 9728
    meta_url = None
    match = re.search(r'resource_metadata="([^"]+)"', www_auth)
    if match:
        meta_url = match.group(1)
    else:
        link_header = resp.headers.get("Link", "")
        link_match = re.search(r'<([^>]+)>;\s*rel="resource-metadata"', link_header)
        if link_match:
            meta_url = link_match.group(1)

    assert meta_url, (
        f"RFC 9728 resource_metadata link not found in headers. WWW-Authenticate: {www_auth!r}, Link: {resp.headers.get('Link')!r}"
    )

    meta_resp = http_session.get(meta_url, timeout=30)
    assert meta_resp.status_code == 200, f"Resource metadata URL {meta_url} answered {meta_resp.status_code}"
    meta = meta_resp.json()
    assert "resource" in meta, f"Metadata must contain 'resource' property: {meta}"
    assert meta["resource"].rstrip("/") == private_mcp_url.rstrip("/"), (
        f"Resource identifier mismatch: {meta['resource']} != {private_mcp_url}"
    )
    assert "authorization_servers" in meta and isinstance(meta["authorization_servers"], list), (
        f"Metadata missing authorization_servers list: {meta}"
    )


def test_sp19_tools_list_remains_stable_under_fixed_grant(mcp: McpClient):
    """SP-19 — Multiple tools/list invocations under identical grant state must return an identical tool set."""
    first = mcp.call("tools/list")
    second = mcp.call("tools/list")

    tools1 = [t["name"] for t in first.get("tools", [])]
    tools2 = [t["name"] for t in second.get("tools", [])]
    assert tools1 == tools2, f"Tool list mutated under static grant: {tools1} != {tools2}"


# --- the hub: one MCP surface over several spaces (T-1209, EP-70, EP-71, AG-30) ---------


def test_ep71_the_hub_says_what_it_federates_before_a_tool_is_called(
    hub_mcp: McpClient, hub_members: list[str]
):
    """EP-71, AG-30: a model reading a hub has no other way to learn that the answer is a
    union, over which sources, and that it can be partial. The instructions say all three,
    and never an address: a member's internal URL in a description would tell a public caller
    where to knock next."""
    instructions = hub_mcp.initialize().get("instructions", "")
    assert instructions, "the hub's MCP surface offers no instructions"
    for member in hub_members:
        assert member in instructions, f"{member!r} is registered but unannounced: {instructions}"
    assert "union" in instructions.lower(), instructions
    assert "partial" in instructions.lower(), instructions
    assert "http://" not in instructions and "https://" not in instructions, instructions


def test_ep71_a_hub_tool_result_names_the_sources_it_is_a_union_over(
    hub_mcp: McpClient, hub_members: list[str]
):
    """EP-71: a merged answer is worth less if a reader cannot tell which city system said
    what. On MCP the carrier is `jc:source`, because a tool answer is read by a model that has
    no other way to attribute it."""
    tools = hub_mcp.tool_names()
    assert "query_entities" in tools, tools
    frame = hub_mcp.tool_call("query_entities", {"type": os.environ.get("HUB_TYPE", "Vehicle")})
    # `tool_call` hands back the whole JSON-RPC frame, so a refusal can be asserted rather than
    # raised; the answer is one level in.
    assert "error" not in frame, frame["error"]
    structured = frame["result"]["structuredContent"]
    sources = structured.get("jc:source")
    assert sources is not None, f"a hub answer carries no jc:source: {structured}"
    assert sorted(sources) == sorted(hub_members), f"{sources} is not the registered set"
    assert structured.get("entities"), "the union answered nothing at all"


def test_pf48_the_hub_grants_no_more_than_its_own_policy_says(hub_mcp: McpClient):
    """PF-48: the hub's broker reads its members' tenants directly, so what a member's own
    Endpoint grants does not travel with the answer. The hub's tool set is therefore the hub's
    policy set and nothing a member would have allowed."""
    for tool in hub_mcp.call("tools/list").get("tools", []):
        annotations = tool.get("annotations", {})
        assert annotations.get("readOnlyHint") is True, (
            f"{tool['name']} is a write tool on a read-only hub: {annotations}"
        )

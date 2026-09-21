"""Data and Configuration MCP isolation and agent governance (T-0061).

AG-05 the space comes from the URL and the token, never from a tool argument; AG-06 the
Configuration MCP proposes merge requests instead of writing the broker, gateway or database;
AG-11 an agent neither approves its own merge request nor edits the approval lanes; AG-13 a
narrowed answer says so; SP-14/SP-20 a cross-space probe is indistinguishable from a miss.
"""

from __future__ import annotations

import typing

import pytest

from conftest import McpClient

# urn:ngsi-ld:{Type}:{orgDomain}:{space}:{localId} — the space is the fourth segment (PF-42)
URN_SPACE_SEGMENT = 4


def space_of(url: str) -> str:
    """The space a per-space MCP URL serves: `https://host/cs/{space}/mcp`."""
    segments = url.rstrip("/").split("/")
    return segments[segments.index("cs") + 1] if "cs" in segments[:-1] else ""


def entities_of(result: dict) -> list[dict]:
    """The entities a query tool returned, whichever of the two MCP result shapes it used."""
    if isinstance(result.get("structuredContent"), dict):
        result = result["structuredContent"]
    for key in ("entities", "results", "items"):
        if isinstance(result.get(key), list):
            return [item for item in result[key] if isinstance(item, dict)]
    return [item for item in result.get("content", []) if isinstance(item, dict) and "id" in item]


def refused(frame: dict) -> bool:
    """A JSON-RPC error, a tool result flagged `isError`, or an elicitation: each one means the
    call was not carried out. An elicitation (`status: input_required`) is the gate answering —
    the server asked the person for something before doing anything (PF-57, AG-62)."""
    if "error" in frame:
        assert isinstance(frame["error"].get("code"), int), f"a refusal needs a JSON-RPC code: {frame['error']}"
        assert frame["error"].get("message"), f"a refusal needs a message: {frame['error']}"
        return True
    result = frame["result"]
    if result.get("isError") is True:
        return True
    body = result.get("structuredContent")
    return result.get("status") == "input_required" or (
        isinstance(body, dict) and "elicitation" in body
    )


def query_tool(client: McpClient) -> str:
    """The read tool the isolation tests drive, skipping when the surface exposes none (SP-15)."""
    names = client.tool_names()
    for candidate in ("query_entities", "search_entities", "list_entities"):
        if candidate in names:
            return candidate
    pytest.skip(f"no query tool on this MCP surface, only {names}")


def test_ag05_sp14_a_space_argument_never_moves_the_caller(
    mcp: McpClient,
    mcp_url: str,
    other_space_mcp_url: str,
    other_space_query: dict,
    mcp_token: str | None,
    make_mcp_client: typing.Callable[..., McpClient],
):
    """AG-05, SP-14 — `space`/`tenant` passed as a tool argument is data, never a selector."""
    own = space_of(mcp_url)
    assert own, f"MCP_URL is not a per-space MCP URL: {mcp_url}"
    foreign = space_of(other_space_mcp_url)
    assert foreign != own, f"OTHER_SPACE_MCP_URL serves the same space as MCP_URL: {foreign}"
    tool = query_tool(mcp)

    # without this the assertion below would pass on a foreign space that simply holds nothing
    other = make_mcp_client(other_space_mcp_url, mcp_token)
    other.initialize()
    foreign_frame = other.tool_call(tool, {"limit": 5, **other_space_query})
    assert not refused(foreign_frame), f"the foreign space refused the read, the probe would be vacuous: {foreign_frame}"
    assert entities_of(foreign_frame["result"]), (
        f"{other_space_mcp_url} returned no entity, so 'the argument did not reach it' proves nothing"
    )

    for argument in ({"space": foreign}, {"tenant": foreign}, {"space": foreign, "type": "Device"}):
        frame = mcp.tool_call(tool, {"limit": 10, **argument})
        if refused(frame):
            continue
        for entity in entities_of(frame["result"]):
            identifier = str(entity.get("id", ""))
            segments = identifier.split(":")
            assert foreign not in segments, f"AG-05: {argument} reached {foreign} — {identifier}"
            if len(segments) > URN_SPACE_SEGMENT:
                assert segments[URN_SPACE_SEGMENT] == own, (
                    f"AG-05: {argument} returned {identifier}, which lives in {segments[URN_SPACE_SEGMENT]}, not {own}"
                )


def test_sp20_a_cross_space_probe_is_byte_identical_to_a_miss(
    mcp: McpClient,
    mcp_url: str,
    other_space_mcp_url: str,
    other_space_query: dict,
    mcp_token: str | None,
    make_mcp_client: typing.Callable[..., McpClient],
):
    """SP-20 — asking for an entity of another space answers exactly what an invented id answers."""
    own = space_of(mcp_url)
    foreign = space_of(other_space_mcp_url)
    assert "get_entity" in mcp.tool_names(), "the surface exposes no get_entity to probe with"

    other = make_mcp_client(other_space_mcp_url, mcp_token)
    other.initialize()
    found = entities_of(other.tool_call(query_tool(other), {"limit": 1, **other_space_query})["result"])
    assert found, f"{other_space_mcp_url} holds no entity to probe for, the comparison would be vacuous"
    foreign_id = found[0]["id"]
    assert foreign_id.split(":")[URN_SPACE_SEGMENT] != own, f"the probe id {foreign_id} belongs to the caller's own space"

    invented_id = f"urn:ngsi-ld:Device:joinedcontext.com:{foreign}:probe-{'9' * 12}"
    # the same request id in both, so only the server's own words can differ
    probe = mcp.post({"jsonrpc": "2.0", "id": 4711, "method": "tools/call",
                      "params": {"name": "get_entity", "arguments": {"id": foreign_id}}}, raw=True)
    miss = mcp.post({"jsonrpc": "2.0", "id": 4711, "method": "tools/call",
                     "params": {"name": "get_entity", "arguments": {"id": invented_id}}}, raw=True)

    assert probe.status_code == miss.status_code, (
        f"SP-20: an entity of {foreign} answers HTTP {probe.status_code}, an invented id {miss.status_code}"
    )
    assert probe.text.replace(foreign_id, invented_id) == miss.text, (
        f"SP-20: the cross-space probe is distinguishable from a miss\nprobe: {probe.text}\nmiss:  {miss.text}"
    )


def test_ag06_the_configuration_mcp_writes_nothing_directly(config_mcp: McpClient, config_mcp_scratch: str | None):
    """AG-06 — no tool writes the broker, gateway or database; a change comes back as a merge request."""
    names = config_mcp.tool_names()
    assert names, "the Configuration MCP exposes no tool at all"
    for name in names:
        for forbidden in ("sql", "broker_write", "write_broker", "direct_write", "db_write", "write_db", "force_apply"):
            assert forbidden not in name.lower(), f"AG-06: {name} writes past the merge request"

    if not config_mcp_scratch:
        pytest.skip("CONFIG_MCP_SCRATCH is not set — see tests/mcp/README.md")

    proposal = config_mcp.tool_call(
        "propose_change",
        {
            "path": f"spaces/{config_mcp_scratch}/README.md",
            "content": "conformance probe (T-0061)\n",
            "message": "conformance: AG-06 probe",
        },
    )
    assert not refused(proposal), f"AG-06: the configuration plane refused a legitimate proposal: {proposal}"
    result = proposal["result"]
    body = result.get("structuredContent", result)
    reference = {key.lower().replace("_", "") for key in body} & {
        "mergerequest", "mergerequesturl", "pullrequest", "pullrequesturl", "branch", "commit", "url"
    }
    assert reference, f"AG-06: propose_change named no merge request, branch or commit: {body}"
    assert body.get("applied") is not True and body.get("state") != "applied", (
        f"AG-06: propose_change applied the change instead of proposing it: {body}"
    )


def test_ag11_an_agent_neither_approves_itself_nor_edits_the_lanes(agent_config_mcp: McpClient):
    """AG-11 — self-approval and lane configuration are refused for an agent identity (CC-70)."""
    names = agent_config_mcp.tool_names()
    attempts = [
        ("approve_merge_request", {"id": "self"}),
        ("approve_mr", {"id": "self"}),
        ("update_approval_lane", {"lane": "red", "autoApprove": True}),
        ("set_risk_class", {"blueprint": "any", "riskClass": "green"}),
    ]
    exercised = 0
    for name, arguments in attempts:
        frame = agent_config_mcp.tool_call(name, arguments)
        if "error" in frame and frame["error"].get("code") == -32601:
            continue  # the tool does not exist on the agent surface, which is the strongest form of refusal
        exercised += 1
        assert refused(frame), f"AG-11: an agent identity carried out {name}: {frame}"
    assert exercised or not ({name for name, _ in attempts} & set(names)), (
        f"AG-11: {names} advertises an approval tool that answered nothing to assert on"
    )


def test_ag13_a_narrowed_answer_says_so_and_leaks_nothing(
    mcp: McpClient, restricted_type: str, hidden_attr: str
):
    """AG-13, R20/R22 — a policy-narrowed tool result carries `restricted: true` and drops the hidden attribute."""
    frame = mcp.tool_call(query_tool(mcp), {"type": restricted_type, "limit": 25})
    assert not refused(frame), f"the narrowed query was refused instead of narrowed: {frame}"
    result = frame["result"]
    body = result.get("structuredContent", result)
    entities = entities_of(result)
    assert entities, f"{restricted_type} returned nothing, so narrowing cannot be observed"

    for entity in entities:
        assert hidden_attr not in entity, f"AG-13: {entity.get('id')} still carries the hidden attribute {hidden_attr}"
    assert body.get("restricted") is True or result.get("restricted") is True, (
        f"AG-13: the answer was narrowed (no {hidden_attr} anywhere) but carries no `restricted: true`: {body}"
    )

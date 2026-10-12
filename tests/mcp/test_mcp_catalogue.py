"""EP-112 (T-4115): one tool catalogue on every MCP surface.

The Endpoint's own MCP (`ENDPOINT_MCP_URL`, the broker's view) is the reference. A space's MCP
(`MCP_URL`), the hub (`CONNECTOR_MCP_URL`, `/api/mcp`) and a named server (`NAMED_MCP_URL`,
`/api/mcp/{project}/{name}`) list `tools/list` for the same token, and every tool they share with
the reference carries the same argument names and argument types. The hub and a named server may
add only `list_endpoints` and the `endpoint` argument; a named server's read tools also take a
cursor per member, so an argument type there passes when the reference's type is one of its
alternatives.
"""

from __future__ import annotations

import os
import typing

import pytest

from conftest import McpClient

#: The tools no Endpoint's own MCP lists: the hub's directory of Endpoints.
CONNECTOR_ONLY = {"list_endpoints"}
#: The argument no Endpoint's own MCP takes: which Endpoint a hub call is for.
CONNECTOR_ARGS = {"endpoint"}


def types_of(schema: dict) -> set[str]:
    """The JSON types one argument may take, its alternatives included."""
    found: set[str] = set()
    kind = schema.get("type")
    if isinstance(kind, str):
        found.add(kind)
    elif isinstance(kind, list):
        found.update(k for k in kind if isinstance(k, str))
    for alternative in schema.get("oneOf", []) + schema.get("anyOf", []):
        if isinstance(alternative, dict):
            found |= types_of(alternative)
    return found


def catalogue(tools: list[dict]) -> dict[str, dict[str, set[str]]]:
    """Each tool by name: each argument by name with the types it takes."""
    out: dict[str, dict[str, set[str]]] = {}
    for tool in tools:
        properties = (tool.get("inputSchema") or {}).get("properties") or {}
        out[tool["name"]] = {name: types_of(schema) for name, schema in properties.items()}
    return out


def differences(
    reference: dict[str, dict[str, set[str]]],
    surface: dict[str, dict[str, set[str]]],
    *,
    connector: bool,
) -> list[str]:
    """What `surface` lists otherwise than `reference`, one line per difference."""
    extra_tools = CONNECTOR_ONLY if connector else set()
    extra_args = CONNECTOR_ARGS if connector else set()
    problems = [f"{name}: not listed" for name in sorted(reference.keys() - surface.keys())]
    problems += [
        f"{name}: listed only here" for name in sorted(surface.keys() - reference.keys() - extra_tools)
    ]
    for name in sorted(reference.keys() & surface.keys()):
        theirs, ours = reference[name], {k: v for k, v in surface[name].items() if k not in extra_args}
        problems += [f"{name}.{arg}: missing" for arg in sorted(theirs.keys() - ours.keys())]
        problems += [f"{name}.{arg}: an argument only here" for arg in sorted(ours.keys() - theirs.keys())]
        for arg in sorted(theirs.keys() & ours.keys()):
            if theirs[arg] and not theirs[arg] <= ours[arg]:
                problems.append(f"{name}.{arg}: {sorted(ours[arg])} where the Endpoint takes {sorted(theirs[arg])}")
    return problems


def listed(url: str, token: str | None, session: typing.Any) -> dict[str, dict[str, set[str]]]:
    client = McpClient(url, token, session)
    client.initialize()
    return catalogue(client.call("tools/list", {}).get("tools", []))


SURFACES = [
    ("MCP_URL", False),
    ("CONNECTOR_MCP_URL", True),
    ("NAMED_MCP_URL", True),
]


@pytest.mark.platform_only
@pytest.mark.parametrize(("variable", "connector"), SURFACES, ids=[v for v, _ in SURFACES])
def test_ep112_every_surface_lists_the_endpoint_s_tools(
    variable: str,
    connector: bool,
    endpoint_mcp_url: str,
    mcp_token: str | None,
    http_session: typing.Any,
) -> None:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} is not set — see tests/mcp/README.md")
    reference = listed(endpoint_mcp_url, mcp_token, http_session)
    assert reference, "the Endpoint's own MCP lists no tool for this token"
    problems = differences(reference, listed(url.rstrip("/"), mcp_token, http_session), connector=connector)
    assert not problems, f"{variable} lists another catalogue (EP-112):\n" + "\n".join(problems)


def test_the_diff_names_what_differs() -> None:
    """The rule itself, on lists written out: a renamed tool, a renamed argument, a string where
    a list is taken and a hub-only argument on a space's MCP each name one line."""
    reference = catalogue([
        {"name": "query_entities", "inputSchema": {"properties": {"type": {"type": "array"}, "cursor": {"type": "string"}}}},
        {"name": "list_type_details", "inputSchema": {"properties": {}}},
    ])
    same = catalogue([
        {"name": "list_endpoints", "inputSchema": {"properties": {"q": {"type": "string"}}}},
        {"name": "query_entities", "inputSchema": {"properties": {
            "endpoint": {"type": "string"}, "type": {"type": "array"},
            "cursor": {"oneOf": [{"type": "string"}, {"type": "object"}]},
        }}},
        {"name": "list_type_details", "inputSchema": {"properties": {"endpoint": {"type": "string"}}}},
    ])
    assert differences(reference, same, connector=True) == []
    other = catalogue([
        {"name": "query_entities", "inputSchema": {"properties": {
            "endpoint": {"type": "string"}, "type": {"type": "string"}, "ids": {"type": "array"},
        }}},
        {"name": "list_types", "inputSchema": {"properties": {"details": {"type": "boolean"}}}},
    ])
    assert differences(reference, other, connector=False) == [
        "list_type_details: not listed",
        "list_types: listed only here",
        "query_entities.cursor: missing",
        "query_entities.endpoint: an argument only here",
        "query_entities.ids: an argument only here",
        "query_entities.type: ['string'] where the Endpoint takes ['array']",
    ]

"""The Portal MCP server over the operation registry (T-0641, AG-60, AG-63, ADR-N-021).

`PORTAL_MCP_URL` names the Portal's `/api/v1/mcp`, `PORTAL_MCP_PROJECT` a project the caller
may draft in; `MCP_TOKEN` is a bearer whose `aud` names the Portal. Every door reaches the same registered operation, so what the REST route refuses
the MCP route refuses too: a proposal without a fresh Verdict answers `verdict_required`.
"""
from __future__ import annotations

import os

import pytest
import requests

from conftest import McpClient
from test_mcp_isolation import refused

UNITS = {"load", "share", "analyse", "model"}


def test_ag60_portal_mcp_refuses_without_a_bearer_and_names_its_metadata(
    http_session: requests.Session, portal_mcp_url: str
):
    """AG-60 / RFC 9728 — an anonymous call is a 401 whose WWW-Authenticate names the
    protected-resource document, and that document is public."""
    resp = http_session.post(
        portal_mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    )
    assert resp.status_code == 401, f"AG-60: an anonymous MCP call answered {resp.status_code}"
    challenge = resp.headers.get("WWW-Authenticate", "")
    assert challenge.startswith("Bearer") and "resource_metadata=" in challenge, (
        f"RFC 9728: WWW-Authenticate names no resource_metadata: {challenge!r}"
    )
    metadata_url = challenge.split('resource_metadata="', 1)[1].split('"', 1)[0]
    doc = http_session.get(metadata_url, timeout=30, allow_redirects=False)
    assert doc.status_code == 200, f"RFC 9728: the metadata document answered {doc.status_code} (a login redirect is not public)"
    body = doc.json()
    assert body.get("resource", "").rstrip("/") == portal_mcp_url.rstrip("/"), body
    assert body.get("authorization_servers"), body
    assert "mcp:portal" in body.get("scopes_supported", []), body


def test_ag60_every_tool_is_a_registry_operation_bound_to_a_project(portal_mcp: McpClient):
    """AG-59/AG-60 — tools are the `jc_` operations, each with an input schema that requires
    the project, each with behaviour annotations."""
    tools = portal_mcp.call("tools/list").get("tools", [])
    assert tools, "the Portal MCP lists no tool for this caller"
    for tool in tools:
        assert tool["name"].startswith("jc_"), f"AG-59: {tool['name']} is not a registry operation"
        schema = tool.get("inputSchema") or {}
        assert "project" in schema.get("required", []), f"{tool['name']}: project is not required: {schema}"
        assert "project" in schema.get("properties", {}), f"{tool['name']}: no project property"
        assert "readOnlyHint" in (tool.get("annotations") or {}), f"AG-07: {tool['name']} carries no annotations"
    assert "jc_catalog_search" in {t["name"] for t in tools}


def test_ag60_resources_are_manifests_drafts_and_schemas_and_every_listed_one_reads(
    portal_mcp: McpClient,
):
    """AG-60 — resources/list names manifests, drafts and the kinds' JSON Schemas; each reads."""
    project = os.getenv("PORTAL_MCP_PROJECT")
    params = {"project": project} if project else None
    listed = portal_mcp.call("resources/list", params).get("resources", [])
    uris = [r["uri"] for r in listed]
    assert any(u.startswith("jc://schemas/") for u in uris), f"no kind schema among the resources: {uris[:5]}"
    for uri in uris[:5] + [u for u in uris if u.startswith("jc://schemas/Endpoint")][:1]:
        contents = portal_mcp.call("resources/read", {"uri": uri}).get("contents", [])
        assert contents and contents[0].get("text"), f"{uri}: resources/read returned nothing"
    missing = portal_mcp.error("resources/read", {"uri": "jc://schemas/NoSuchKind"})
    assert missing["code"] == -32002, f"a missing resource is -32002, got {missing}"


def test_ag63_prompts_are_the_units(portal_mcp: McpClient):
    """AG-63 — the guided units are prompts that take the project and name the operations."""
    prompts = portal_mcp.call("prompts/list").get("prompts", [])
    names = {p["name"] for p in prompts}
    assert UNITS <= names, f"AG-63: the units are not all prompts: {names}"
    got = portal_mcp.call("prompts/get", {"name": "share", "arguments": {"project": "probe"}})
    text = got["messages"][0]["content"]["text"]
    assert "probe" in text and "jc_endpoint_propose" in text, text


def test_pf57_a_proposal_without_a_verdict_is_refused_on_every_door(portal_mcp: McpClient):
    """PF-57 / AG-62 — a draft proposed before its check is refused with verdict_required,
    through MCP exactly as through REST (ADR-N-021)."""
    project = os.getenv("PORTAL_MCP_PROJECT")
    if not project:
        pytest.skip("PORTAL_MCP_PROJECT is not set — see tests/mcp/README.md")
    name = "conformance-probe-verdict"
    manifest = {
        "apiVersion": "joinedcontext.com/v1alpha1",
        "kind": "DataSource",
        "metadata": {"name": name, "namespace": project},
        "spec": {"type": "http", "http": {"url": "https://example.org/conformance.json"}},
    }
    put = portal_mcp.tool_call(
        "jc_draft_put", {"project": project, "kind": "DataSource", "name": name, "manifest": manifest}
    )
    assert not refused(put), f"jc_draft_put refused the probe draft: {put}"
    try:
        proposal = portal_mcp.tool_call(
            "jc_datasource_propose", {"project": project, "draft": {"kind": "DataSource", "name": name}}
        )
        assert refused(proposal), f"PF-57: an unchecked draft was proposed: {proposal}"
        body = proposal.get("result", {}).get("structuredContent") or {}
        text = str(proposal)
        assert body.get("error") == "verdict_required" or "verdict_required" in text, (
            f"PF-57: the refusal does not name verdict_required: {proposal}"
        )
    finally:
        portal_mcp.tool_call("jc_draft_drop", {"project": project, "kind": "DataSource", "name": name})


# --- the copies (workspaces) over MCP ------------------------------------------------------
#
# CC-76 and AG-82 (T-1265): a copy is opened, read, compared and thrown away through the same
# registered operations the REST route serves, so what one door does the other does — except the
# two decisions AG-82 reserves for a person, which the MCP door does not even offer.

COPY = "conf-mcp-copy"


def _copy_names(portal_mcp: McpClient, project: str) -> list[str]:
    listed = portal_mcp.tool_call("jc_workspace_list", {"project": project})
    body = listed.get("result", {}).get("structuredContent") or {}
    items = body.get("items") or body.get("workspaces") or []
    return [item.get("name") for item in items if isinstance(item, dict)]


@pytest.fixture
def project() -> str:
    name = os.getenv("PORTAL_MCP_PROJECT")
    if not name:
        pytest.skip("PORTAL_MCP_PROJECT is not set — see tests/mcp/README.md")
    return name


@pytest.fixture
def portal_rest(portal_mcp_url: str) -> str:
    """The REST root beside the MCP one: the same operations, the other door."""
    return portal_mcp_url.rsplit("/mcp", 1)[0]


@pytest.fixture
def a_copy(portal_mcp: McpClient, project: str, portal_rest: str, http_session: requests.Session):
    """One copy of the project, opened over MCP and taken away over REST whatever the test does.

    The cleanup is the REST route on purpose: AG-82 refuses the discard to an MCP caller, so the
    fixture that opens a copy through the model's door cannot close it through the same one.
    """
    opened = portal_mcp.tool_call("jc_workspace_open", {"project": project, "name": COPY, "ttlDays": 1})
    if refused(opened):
        text = str(opened)
        if "already exists" not in text:
            pytest.fail(f"CC-76: jc_workspace_open refused over MCP: {opened}")
    try:
        yield COPY
    finally:
        http_session.delete(
            f"{portal_rest}/projects/{project}/workspaces/{COPY}",
            headers={"Authorization": f"Bearer {os.getenv('CONFIG_MCP_TOKEN', '')}"},
            timeout=30,
        )


def test_cc76_a_copy_opens_over_mcp_and_lists_itself(portal_mcp: McpClient, project: str, a_copy: str):
    """CC-76 — the copy a model opens is a copy of the project, and it is in the list the same
    door reads."""
    assert a_copy in _copy_names(portal_mcp, project), (
        f"the copy {a_copy} is not in the list the same caller reads"
    )
    got = portal_mcp.tool_call("jc_workspace_get", {"project": project, "name": a_copy})
    assert not refused(got), f"CC-76: the copy does not read back over MCP: {got}"
    body = got.get("result", {}).get("structuredContent") or {}
    record = body.get("workspace") or body
    assert record.get("name") == a_copy, record
    assert record.get("expiresAt"), f"CC-81: the copy does not say when it expires: {record}"
    assert record.get("baseRevision"), f"CC-76: the copy does not say what it was cut from: {record}"


def test_cc79_a_copy_compares_over_mcp(portal_mcp: McpClient, project: str, a_copy: str):
    """CC-79 — the comparison is what a model presents to the person instead of proposing: it
    answers on a copy that changes nothing, with nothing in it."""
    compared = portal_mcp.tool_call("jc_workspace_compare", {"project": project, "name": a_copy})
    assert not refused(compared), f"CC-79: jc_workspace_compare refused over MCP: {compared}"
    body = compared.get("result", {}).get("structuredContent") or {}
    comparison = body.get("comparison") or body
    assert "files" in comparison, f"CC-79: the comparison names no files member: {comparison}"
    assert comparison.get("files") == [], f"a fresh copy changes nothing: {comparison}"
    assert comparison.get("conflicts", []) == [], f"a fresh copy conflicts with nothing: {comparison}"


def test_ag82_neither_way_out_of_a_copy_is_offered_over_mcp(portal_mcp: McpClient, project: str, a_copy: str):
    """AG-82 — bringing a copy back and throwing it away are a person's, so the MCP door does not
    offer either tool, and naming one anyway is refused rather than run."""
    offered = portal_mcp.tool_names()
    for reserved in ["jc_workspace_propose", "jc_workspace_discard"]:
        assert reserved not in offered, f"AG-82: {reserved} is offered to an MCP caller"
        frame = portal_mcp.tool_call(reserved, {"project": project, "name": a_copy})
        assert refused(frame) or "error" in frame, f"AG-82: {reserved} ran over MCP: {frame}"


def test_ag82_a_person_takes_the_copy_away_over_rest(
    portal_mcp: McpClient, project: str, portal_rest: str, http_session: requests.Session, a_copy: str
):
    """AG-82 — the rule is about who, not about whether: the same copy the model may not discard
    is discarded by the person whose token this is, over the REST route."""
    headers = {"Authorization": f"Bearer {os.getenv('CONFIG_MCP_TOKEN', '')}"}
    gone = http_session.delete(
        f"{portal_rest}/projects/{project}/workspaces/{a_copy}", headers=headers, timeout=30
    )
    assert gone.status_code in (200, 204), f"a person cannot discard the copy: {gone.status_code} {gone.text}"
    assert a_copy not in _copy_names(portal_mcp, project), "the copy is still listed after the discard"

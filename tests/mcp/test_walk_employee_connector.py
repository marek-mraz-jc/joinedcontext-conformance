"""An employee connects their own AI client to the Portal MCP with their own login (T-5538).

The protocol half of the walkthrough: what a person's connector token reads, what it may
write, what it is asked and what it is refused (AG-11, AG-60, AG-63, AG-82). The tokens are a
person's, obtained through the public PKCE client the AI vendors use (`mcp-mobile`, scope
`mcp:portal`), never a ServiceAccount's: `WALK_EDITOR_TOKEN` is someone who may propose in
`WALK_PROJECT`, `WALK_VIEWER_TOKEN` someone who may only read it. The tokens stay in the run's
environment; revoke them at the end of the run (README, "A person's connector").

Nothing here writes: every writing call is made without the person's answer, which the server
must turn into a question or a refusal. The rate-limit test runs last and spends the viewer's
minute.
"""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlparse

import pytest
import requests
from conftest import McpClient
from test_mcp_isolation import refused

#: The audiences a connector token may name: the Portal's, and Keycloak's own account client.
CONNECTOR_AUDIENCES = {"portal-api", "account"}
#: AG-60: calls one token subject may make in a minute (src/mcp/mod.rs MAX_CALLS_PER_MINUTE).
CALLS_PER_MINUTE = 120


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(
            f"{name} is not set — see tests/mcp/README.md, 'A person's connector'"
        )
    return value


@pytest.fixture(scope="module")
def project() -> str:
    return _env("WALK_PROJECT")


@pytest.fixture(scope="module")
def editor_token() -> str:
    return _env("WALK_EDITOR_TOKEN")


@pytest.fixture(scope="module")
def viewer_token() -> str:
    return _env("WALK_VIEWER_TOKEN")


def _client(url: str, token: str) -> McpClient:
    client = McpClient(url, token)
    client.initialize()
    return client


@pytest.fixture(scope="module")
def editor(portal_mcp_url: str, editor_token: str) -> McpClient:
    return _client(portal_mcp_url, editor_token)


@pytest.fixture(scope="module")
def viewer(portal_mcp_url: str, viewer_token: str) -> McpClient:
    return _client(portal_mcp_url, viewer_token)


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _tools(client: McpClient, project: str) -> list[dict]:
    return client.call("tools/list", {"project": project}).get("tools", [])


def _structured(frame: dict) -> dict:
    return (frame.get("result") or {}).get("structuredContent") or {}


def _text(frame: dict) -> str:
    result = frame.get("result") or {}
    return " ".join(
        part.get("text", "") for part in result.get("content", [])
    ) or json.dumps(frame.get("error"))


@pytest.mark.parametrize("who", ["editor_token", "viewer_token"])
def test_the_connector_token_names_only_the_portal(
    who: str, request: pytest.FixtureRequest
):
    """A token an AI vendor holds for the person names the Portal and nothing else (T-5611)."""
    claims = _claims(request.getfixturevalue(who))
    assert "mcp:portal" in claims.get("scope", "").split(), (
        f"the token was not asked for mcp:portal: {claims.get('scope')}"
    )
    audiences = claims.get("aud")
    audiences = set([audiences] if isinstance(audiences, str) else audiences or [])
    assert "portal-api" in audiences, (
        f"AG-60: the Portal is not an audience: {sorted(audiences)}"
    )
    assert audiences <= CONNECTOR_AUDIENCES, (
        f"T-5611: the connector token also names {sorted(audiences - CONNECTOR_AUDIENCES)}"
    )


def test_a_viewer_is_offered_no_writing_tool(viewer: McpClient, project: str):
    """A person who may only read is offered only reads (AG-63, T-5608)."""
    tools = _tools(viewer, project)
    assert tools, "the viewer is offered no tool at all, the check would be vacuous"
    writing = sorted(
        t["name"] for t in tools if not (t.get("annotations") or {}).get("readOnlyHint")
    )
    assert writing == [], (
        f"T-5608: a viewer is offered {len(writing)} writing tools: {writing}"
    )


def test_a_viewer_is_refused_before_the_question(viewer: McpClient, project: str):
    """AG-63: a call the person may not run is refused, not turned into a question they would
    answer only to be refused afterwards (T-5608)."""
    frame = viewer.tool_call("jc_project_delete", {"project": project})
    assert refused(frame), f"a viewer's project delete was carried out: {frame}"
    assert (frame.get("result") or {}).get("status") != "input_required", (
        f"T-5608: the viewer is asked to confirm a delete of '{project}' they may not make: {_text(frame)}"
    )


def test_an_editor_delete_without_an_answer_runs_nothing(
    editor: McpClient, project: str, portal_mcp_url: str
):
    """AG-63: a destructive call without the person's answer is a question with a confirm page on
    the Portal, and nothing changes: no Change opens and the resource is still there."""
    endpoints = (
        _structured(
            editor.tool_call(
                "jc_resource_list", {"project": project, "kind": "Endpoint"}
            )
        ).get("items")
        or []
    )
    assert endpoints, (
        f"the editor reads no Endpoint in {project}, the probe would be vacuous"
    )
    name = endpoints[0]["name"]
    before = len(
        _structured(editor.tool_call("jc_change_list", {"project": project})).get(
            "items"
        )
        or []
    )

    frame = editor.tool_call(
        "jc_resource_delete",
        {"project": project, "kind": "Endpoint", "name": name, "confirm": name},
    )

    assert (frame.get("result") or {}).get("status") == "input_required", (
        f"AG-63: the delete was not asked first: {frame}"
    )
    question = _structured(frame).get("elicitation") or {}
    assert question.get("elicitationId"), (
        f"the question has no id to answer: {question}"
    )
    confirm = urlparse(question.get("url", ""))
    assert (
        confirm.scheme == "https"
        and confirm.hostname == urlparse(portal_mcp_url).hostname
    ), f"the confirm page is not on the Portal: {question.get('url')}"
    after = len(
        _structured(editor.tool_call("jc_change_list", {"project": project})).get(
            "items"
        )
        or []
    )
    assert after == before, (
        f"a Change opened without the person's answer ({before} -> {after})"
    )
    still = editor.tool_call(
        "jc_resource_get", {"project": project, "kind": "Endpoint", "name": name}
    )
    assert not refused(still), (
        f"the Endpoint {name} is gone after an unanswered delete: {still}"
    )


def test_an_ai_client_never_approves(editor: McpClient, project: str):
    """AG-11: a Change is decided by a person, never by the client holding their token."""
    assert "jc_change_approve" not in {t["name"] for t in _tools(editor, project)}
    frame = editor.tool_call(
        "jc_change_approve", {"project": project, "name": "walk-probe-none"}
    )
    assert refused(frame), f"AG-11: jc_change_approve ran over MCP: {frame}"
    assert "AG-11" in _text(frame), f"the refusal does not name AG-11: {_text(frame)}"


@pytest.mark.parametrize("tool", ["jc_workspace_propose", "jc_workspace_discard"])
def test_an_ai_client_never_brings_a_workspace_back_or_throws_it_away(
    editor: McpClient, project: str, tool: str
):
    """AG-82: bringing a workspace back and discarding it are a person's in the Portal."""
    assert tool not in {t["name"] for t in _tools(editor, project)}
    frame = editor.tool_call(tool, {"project": project, "name": "walk-probe-none"})
    assert refused(frame), f"AG-82: {tool} ran over MCP: {frame}"
    assert "AG-82" in _text(frame), f"the refusal does not name AG-82: {_text(frame)}"


def test_every_call_is_in_activity_with_channel_mcp(
    editor: McpClient, editor_token: str, project: str
):
    """AG-56: what a person's AI client did is on the project's activity, as theirs and as MCP
    (T-5609)."""
    subject = _claims(editor_token)["sub"]
    editor.tool_call("jc_resource_list", {"project": project, "kind": "Endpoint"})
    rows = (
        _structured(
            editor.tool_call(
                "jc_activity_list",
                {"project": project, "kind": "mcp.tool", "limit": 200},
            )
        ).get("items")
        or []
    )
    mine = [
        row
        for row in rows
        if (row.get("details") or {}).get("subject") == subject
        and (row.get("details") or {}).get("operation") == "jc_resource_list"
    ]
    assert mine, (
        f"T-5609: the editor's jc_resource_list is not on the activity ({len(rows)} mcp.tool rows)"
    )
    assert all((row.get("details") or {}).get("channel") == "mcp" for row in mine), (
        f"T-5609: the rows name no channel: {mine[:2]}"
    )


def test_the_121st_call_in_a_minute_is_429_with_retry_after(
    portal_mcp_url: str, viewer_token: str
):
    """AG-60: 120 calls a minute per token subject. Spends the viewer's budget, so it runs last:
    it first drains the current window, waits for a fresh one and then counts from its start."""
    headers = {
        "Authorization": f"Bearer {viewer_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    # A plain session, not RateLimitedSession: the 429 is what is asserted.
    with requests.Session() as http:

        def ping(i: int) -> requests.Response:
            body = json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping"})
            return http.post(portal_mcp_url, data=body, headers=headers, timeout=30)

        drained = next(
            (r for r in map(ping, range(CALLS_PER_MINUTE + 1)) if r.status_code == 429),
            None,
        )
        assert drained is not None, (
            f"AG-60: {CALLS_PER_MINUTE + 1} calls in a minute were all answered"
        )
        time.sleep(int(drained.headers.get("Retry-After", "60")) + 1)

        answers = [ping(i).status_code for i in range(CALLS_PER_MINUTE)]
        assert answers == [200] * CALLS_PER_MINUTE, (
            f"a call inside the budget was refused: {answers.count(429)} 429s"
        )
        last = ping(CALLS_PER_MINUTE)
        assert last.status_code == 429, (
            f"AG-60: call {CALLS_PER_MINUTE + 1} answered {last.status_code}"
        )
        retry = last.headers.get("Retry-After", "")
        assert retry.isdigit() and 0 < int(retry) <= 60, (
            f"the 429 names no usable Retry-After: {retry!r}"
        )

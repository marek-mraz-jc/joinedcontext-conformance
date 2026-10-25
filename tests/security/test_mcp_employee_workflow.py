"""A municipality employee's MCP client, live (T-5559; AG-20, AG-21, AG-25, AG-133).

Run against a throwaway stack: the gateway in front of Antares, a Helsinki editor and a viewer
signed in. The suite proves three things an employee's Claude Desktop or Copilot relies on:

1. `tools/list` on the hub and on one Endpoint's MCP lists exactly the tools the role's Policy
   grants (`EDITOR_*_TOOLS`, `VIEWER_*_TOOLS`: comma lists the stack's seed grants).
2. A viewer, whose Policy grants reads only, is offered no write tool on either surface.
3. An instruction planted in an attribute value comes back as data: the result carries
   `_meta["jc/untrusted"]` and every text block holds the value only inside a fence under a
   one-line preface, while `structuredContent` keeps it unchanged.

Environment: MCP_HUB_URL, MCP_ENDPOINT_URL, TOKEN_EDITOR, TOKEN_VIEWER, PLANT_URL (the
Endpoint's NGSI-LD base, where the editor may create), PLANT_TYPE (default AirQualityObserved),
and per role and surface the expected tool lists: EDITOR_HUB_TOOLS, EDITOR_ENDPOINT_TOOLS,
VIEWER_HUB_TOOLS, VIEWER_ENDPOINT_TOOLS. A missing variable skips its case.
`selftest_mcp_employee.py` runs the suite against `stub_mcp_employee_server.py`, conforming and
with the marker or the filter removed.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from corpus import load

MARKER = "jc/untrusted"

# Antares' write tools (antares-mcp tools.rs): never offered to a read-only role.
WRITE_TOOLS = frozenset(
    {
        "create_entity", "update_attributes", "append_attributes", "merge_entity",
        "replace_attribute", "update_attribute", "delete_attribute", "delete_entity",
        "batch_create", "batch_upsert", "batch_update", "batch_delete",
        "create_subscription", "update_subscription", "delete_subscription",
    }
)


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


def _tools(name: str) -> set[str]:
    return {t.strip() for t in _env(name).split(",") if t.strip()}


class Mcp:
    """One MCP session over Streamable HTTP with a bearer token."""

    def __init__(self, url: str, token: str):
        self.url, self.token, self.session, self.n = url, token, None, 0

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        body = {"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}
        resp = requests.post(self.url, json=body, headers=headers, timeout=30)
        assert resp.status_code == 200, f"{method} on {self.url}: HTTP {resp.status_code} {resp.text[:300]}"
        self.session = resp.headers.get("Mcp-Session-Id", self.session)
        text = resp.text
        if resp.headers.get("Content-Type", "").startswith("text/event-stream"):
            frames = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = frames[-1] if frames else "{}"
        frame = json.loads(text)
        assert "error" not in frame, f"{method} on {self.url}: {frame['error']}"
        return frame.get("result", {})

    def open(self) -> Mcp:
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "employee-workflow-conformance", "version": "1.0.0"},
            },
        )
        return self

    def listed(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                return tools


@pytest.mark.parametrize("role", ["editor", "viewer"])
@pytest.mark.parametrize("surface,url_env", [("hub", "MCP_HUB_URL"), ("endpoint", "MCP_ENDPOINT_URL")])
def test_tools_list_matches_the_policy_per_role(role: str, surface: str, url_env: str):
    expected = _tools(f"{role.upper()}_{surface.upper()}_TOOLS")
    client = Mcp(_env(url_env), _env(f"TOKEN_{role.upper()}")).open()
    listed = {t["name"] for t in client.listed()}
    assert listed == expected, (
        f"{role} on the {surface}: offered but not granted {sorted(listed - expected)}, "
        f"granted but not offered {sorted(expected - listed)}"
    )


@pytest.mark.parametrize("surface,url_env", [("hub", "MCP_HUB_URL"), ("endpoint", "MCP_ENDPOINT_URL")])
def test_a_viewer_is_offered_no_write(surface: str, url_env: str):
    client = Mcp(_env(url_env), _env("TOKEN_VIEWER")).open()
    tools = client.listed()
    assert tools, f"the viewer is offered no tool on the {surface}, so no write proves nothing"
    writes = [
        t["name"]
        for t in tools
        if t["name"] in WRITE_TOOLS or (t.get("annotations") or {}).get("readOnlyHint") is not True
    ]
    assert not writes, f"a read-only viewer is offered writes on the {surface}: {writes}"


def _fenced(text: str) -> tuple[str, str] | None:
    """(preface, inside) of a text block that is one fence under one line, else None."""
    lines = text.split("\n")
    if len(lines) < 4 or lines[0].startswith("`"):
        return None
    fence = lines[1][: len(lines[1]) - len(lines[1].lstrip("`"))]
    if len(fence) < 3 or lines[-1] != fence:
        return None
    inside = lines[2:-1]
    if any(line.startswith(fence) for line in inside):
        return None
    return lines[0], "\n".join(inside)


def test_a_planted_instruction_comes_back_as_marked_data():
    vector = next(v for v in load(_HERE / "injection_corpus.yaml") if v.placement == "attribute-value")
    kind = os.getenv("PLANT_TYPE", "AirQualityObserved")
    entity_id = f"urn:ngsi-ld:{kind}:t5559-{uuid.uuid4().hex[:12]}"
    plant, token = _env("PLANT_URL").rstrip("/"), _env("TOKEN_EDITOR")
    client = Mcp(_env("MCP_ENDPOINT_URL"), token).open()
    auth = {"Authorization": f"Bearer {token}"}
    made = requests.post(
        f"{plant}/entities",
        json={"id": entity_id, "type": kind, "description": {"type": "Property", "value": vector.payload}},
        headers={**auth, "Content-Type": "application/json"},
        timeout=30,
    )
    assert made.status_code == 201, f"the editor could not plant {vector.id}: HTTP {made.status_code} {made.text[:300]}"
    try:
        result = client.rpc("tools/call", {"name": "get_entity", "arguments": {"id": entity_id}})
        assert result.get("isError") is not True, f"get_entity was refused: {result}"
        assert (result.get("_meta") or {}).get(MARKER) is True, f"no {MARKER} marker: {result.get('_meta')}"
        # the value as it reads inside JSON text
        literal = json.dumps(vector.payload, ensure_ascii=False)[1:-1]
        stored = json.dumps(result.get("structuredContent"), ensure_ascii=False)
        assert literal in stored, "structuredContent lost the stored value"
        blocks = [b for b in result.get("content", []) if b.get("type") == "text"]
        assert blocks, "the result has no text block"
        for block in blocks:
            parts = _fenced(block["text"])
            assert parts, f"a text block is not one fence under a preface: {block['text'][:300]}"
            preface, _inside = parts
            assert literal not in preface and vector.payload not in preface, "the planted value escaped into the preface"
            assert "data" in preface.lower(), f"the preface does not call the block data: {preface}"
        assert any(literal in _fenced(b["text"])[1] for b in blocks), "the value is in no fence"
    finally:
        requests.delete(f"{plant}/entities/{entity_id}", headers=auth, timeout=30)

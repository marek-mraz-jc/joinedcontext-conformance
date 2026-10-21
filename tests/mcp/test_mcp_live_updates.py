"""An agent finds an Endpoint's live updates through its MCP surface (T-5045, API/07).

`describe_access` (format `permissions`) carries `liveUpdates` where the Endpoint serves the
WebSocket: its public `wss://` address beside the MCP door, the `ngsi-ld-ws` subprotocol, one
`x-subscribe` message and the limits. An agent that follows it is welcomed with `x-welcome`.
Where `liveUpdates` is absent, the Endpoint's `/ws` answers 404: the gateway never hides a socket
it serves. Read-only: nothing is subscribed.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.platform_only

TIMEOUT = 20


@pytest.fixture
def endpoint_mcp(make_mcp_client, mcp_token):
    url = os.getenv("ENDPOINT_MCP_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("ENDPOINT_MCP_URL is not set, see tests/mcp/README.md")
    client = make_mcp_client(url, mcp_token)
    instructions = client.initialize().get("instructions", "")
    return url, client, instructions


def test_describe_access_names_the_websocket_an_agent_can_open(
    endpoint_mcp, mcp_token, http_session
):
    url, client, instructions = endpoint_mcp
    frame = client.tool_call("describe_access", {"format": "permissions"})
    assert "result" in frame and not frame["result"].get("isError"), frame
    access = frame["result"]["structuredContent"]["access"]
    live = access.get("liveUpdates")
    door = url.removesuffix("/mcp")
    if live is None:
        answer = http_session.get(f"{door}/ws", timeout=TIMEOUT)
        assert answer.status_code == 404, (
            f"{door}/ws answers {answer.status_code} but describe_access names no liveUpdates"
        )
        return

    expected = (
        urlsplit(door)
        ._replace(scheme="wss" if urlsplit(door).scheme == "https" else "ws")
        .geturl()
        + "/ws"
    )
    assert live["address"] == expected, live
    assert live["protocol"] == "ngsi-ld-ws", live
    assert live["subscribe"]["metadata"]["x-op"] == "x-subscribe", live
    assert live["limits"]["connectionsPerCaller"] >= 1, live
    assert live["address"] in instructions, "initialize does not name the WebSocket"
    if mcp_token:
        assert mcp_token not in json.dumps(live), (
            "the access document repeats the caller's token"
        )

    from websockets.sync.client import connect

    headers = {"Authorization": f"Bearer {mcp_token}"} if mcp_token else {}
    with connect(
        live["address"],
        subprotocols=[live["protocol"]],
        additional_headers=headers,
        open_timeout=TIMEOUT,
    ) as ws:
        welcome = json.loads(ws.recv(timeout=TIMEOUT))
    metadata = welcome.get("metadata", {})
    assert metadata.get("x-op") == "x-welcome", welcome
    assert metadata.get("x-resume-token"), welcome

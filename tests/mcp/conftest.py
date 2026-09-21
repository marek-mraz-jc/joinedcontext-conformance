"""Fixtures for Model Context Protocol (MCP) conformance suites (T-0060, T-0061).

Provides an HTTP-based JSON-RPC client implementing MCP Streamable HTTP semantics, session
token management, and endpoints for per-space, endpoint-specific, configuration-plane,
and RFC 9728 private MCP surfaces.
"""

from __future__ import annotations

import json
import os
import typing
import pytest
import requests

from rate_limit import RateLimitedSession


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        pytest.skip(f"{name} is not set — see tests/mcp/README.md")
    return val


@pytest.fixture(scope="session")
def mcp_url() -> str:
    return _require_env("MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def endpoint_mcp_url() -> str:
    return _require_env("ENDPOINT_MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def hub_mcp_url() -> str:
    """The MCP surface of a hub endpoint: a space that holds only registrations (T-1209,
    EP-70, EP-71). Skipped where the instance federates nothing."""
    return _require_env("HUB_MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def hub_members() -> list[str]:
    """The registration names the hub is a union over, as the manifests call them. Named by
    the caller rather than discovered, so a member that silently stopped being registered
    fails the suite instead of shrinking the expectation."""
    return [name for name in os.getenv("HUB_MEMBERS", "").split(",") if name]


@pytest.fixture(scope="session")
def config_mcp_url() -> str:
    return _require_env("CONFIG_MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def other_space_mcp_url() -> str:
    # Required, not optional: without a second space the isolation probes have nothing to prove
    # the caller did not reach, and they passed on nothing (T-2573).
    return _require_env("OTHER_SPACE_MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def private_mcp_url() -> str:
    return _require_env("PRIVATE_MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def mcp_token() -> str | None:
    return os.getenv("MCP_TOKEN")


@pytest.fixture(scope="session")
def config_mcp_token(mcp_token: str | None) -> str | None:
    """The bearer of the configuration plane. It is a separate variable because the
    configuration MCP and the data MCP are different resources: a deployment that binds a
    token to one audience per resource (RFC 8707) cannot present the same bearer to both.
    Falls back to `MCP_TOKEN` for a deployment where one token reaches everything."""
    return os.getenv("CONFIG_MCP_TOKEN") or mcp_token


@pytest.fixture(scope="session")
def mcp_agent_token() -> str:
    return _require_env("MCP_AGENT_TOKEN")


@pytest.fixture(scope="session")
def narrowed_token(mcp_token: str | None) -> str | None:
    """A bearer whose grant sees less than `MCP_TOKEN`'s, for the read parity suite (T-1860).

    Falls back to `MCP_TOKEN` rather than skipping: the parity a suite asserts holds for any
    grant, and a deployment that has no second identity still gets the assertion run. Where
    the variable is set, the same table is proved for a grant that is actually narrower."""
    return os.getenv("MCP_NARROWED_TOKEN") or mcp_token


@pytest.fixture(scope="session")
def portal_mcp_url() -> str:
    """The Portal MCP server over the operation registry (T-0637, AG-60), `/api/v1/mcp`."""
    return _require_env("PORTAL_MCP_URL").rstrip("/")


@pytest.fixture(scope="session")
def portal_mcp_project() -> str | None:
    return os.getenv("PORTAL_MCP_PROJECT")


@pytest.fixture(scope="session")
def config_mcp_scratch() -> str | None:
    return os.getenv("CONFIG_MCP_SCRATCH")


@pytest.fixture(scope="session")
def other_space_query() -> dict:
    """What a read of the other space has to say to return anything. A query tool takes the type
    as a required argument (an NGSI-LD query names a type, an id or an attribute), and the other
    space holds other types than this one, so the probe that proves a cross-space read is not
    vacuous needs a type of its own."""
    other = os.getenv("OTHER_SPACE_TYPE")
    return {"type": other} if other else {}


@pytest.fixture(scope="session")
def restricted_type() -> str:
    return os.getenv("MCP_RESTRICTED_TYPE", "AirQualityObserved")


@pytest.fixture(scope="session")
def hidden_attr() -> str:
    return _require_env("MCP_HIDDEN_ATTR")


@pytest.fixture(scope="session")
def http_session() -> requests.Session:
    s = RateLimitedSession()
    with s:
        yield s


class McpClient:
    """Streamable HTTP JSON-RPC client for Model Context Protocol."""

    def __init__(self, url: str, token: str | None = None, session: requests.Session | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self.http = session or RateLimitedSession()
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._request_id: int = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _parse_sse(self, text: str) -> dict | list:
        data_lines = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            raise AssertionError(f"SSE response contained no data lines: {text!r:.200}")
        payload_str = "\n".join(data_lines)
        try:
            return json.loads(payload_str)
        except Exception as exc:
            raise AssertionError(
                f"Malformed JSON in SSE stream: {exc}; data: {payload_str!r:.200}"
            ) from exc

    def post(
        self,
        payload: dict | list | str | bytes,
        *,
        headers: dict | None = None,
        session: bool = True,
        raw: bool = False,
    ) -> dict | list | requests.Response:
        req_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.token:
            req_headers["Authorization"] = f"Bearer {self.token}"
        if self.protocol_version:
            req_headers["MCP-Protocol-Version"] = self.protocol_version
        if session and self.session_id:
            req_headers["Mcp-Session-Id"] = self.session_id
        if headers:
            req_headers.update(headers)

        req_headers = {k: v for k, v in req_headers.items() if v != ""}

        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = payload

        resp = self.http.post(self.url, data=body, headers=req_headers, timeout=30)

        sess_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sess_id:
            self.session_id = sess_id

        if raw:
            return resp

        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return self._parse_sse(resp.text)
        try:
            return resp.json()
        except Exception as exc:
            raise AssertionError(
                f"Failed to decode JSON-RPC response from {self.url} (HTTP {resp.status_code}): {exc}; body: {resp.text!r:.200}"
            ) from exc

    def call(self, method: str, params: dict | None = None) -> typing.Any:
        req_id = self._next_id()
        payload: dict[str, typing.Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        frame = self.post(payload, raw=False)
        assert isinstance(frame, dict), f"JSON-RPC frame is {type(frame).__name__}, expected dict: {frame!r:.200}"
        if "error" in frame:
            raise AssertionError(f"JSON-RPC error in {method}: {frame['error']}")
        if "result" not in frame:
            raise AssertionError(f"JSON-RPC frame for {method} has neither result nor error: {frame}")
        return frame["result"]

    def error(self, method: str, params: dict | None = None) -> dict:
        req_id = self._next_id()
        payload: dict[str, typing.Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        resp = self.post(payload, raw=True)
        try:
            frame = resp.json()
        except Exception as exc:
            raise AssertionError(
                f"Expected JSON-RPC error frame, but response was not JSON (HTTP {resp.status_code}): {resp.text!r:.200}"
            ) from exc
        assert isinstance(frame, dict), f"Expected dict frame, got {type(frame).__name__}"
        assert "error" in frame, f"Expected JSON-RPC error in {method}, got: {frame}"
        return frame["error"]

    def tool_call(self, name: str, arguments: dict | None = None) -> dict:
        """The raw JSON-RPC frame of a `tools/call`, so a refusal can be asserted instead of raised."""
        frame = self.post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        assert isinstance(frame, dict), f"tools/call {name} answered {type(frame).__name__}, expected a frame: {frame!r:.200}"
        assert ("result" in frame) ^ ("error" in frame), f"tools/call {name} answered neither exactly one result nor error: {frame}"
        return frame

    def tool_names(self) -> list[str]:
        return [tool["name"] for tool in self.call("tools/list").get("tools", [])]

    def notify(self, method: str, params: dict | None = None) -> requests.Response:
        payload: dict[str, typing.Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return self.post(payload, raw=True)

    def initialize(self) -> dict:
        req_id = self._next_id()
        offered_version = "2026-07-28"
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": offered_version,
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {},
                },
                "clientInfo": {
                    "name": "jc-conformance",
                    "version": "1.0.0",
                },
            },
        }
        frame = self.post(payload, session=False, raw=False)
        if "error" in frame:
            raise AssertionError(f"initialize failed with JSON-RPC error: {frame['error']}")
        result = frame.get("result", {})
        negotiated = result.get("protocolVersion")
        if not negotiated:
            raise AssertionError(f"initialize response missing protocolVersion: {result}")
        self.protocol_version = negotiated
        init_notif = self.notify("notifications/initialized")
        assert init_notif.status_code in (200, 202, 204), (
            f"notifications/initialized returned {init_notif.status_code}"
        )
        return result


@pytest.fixture
def make_mcp_client(http_session: requests.Session) -> typing.Callable[..., McpClient]:
    def _make(url: str, token: str | None = None) -> McpClient:
        return McpClient(url, token, http_session)

    return _make


@pytest.fixture
def uninit_mcp(mcp_url: str, mcp_token: str | None, http_session: requests.Session) -> McpClient:
    return McpClient(mcp_url, mcp_token, http_session)


@pytest.fixture
def mcp(uninit_mcp: McpClient) -> McpClient:
    uninit_mcp.initialize()
    return uninit_mcp


@pytest.fixture
def endpoint_mcp(endpoint_mcp_url: str, mcp_token: str | None, http_session: requests.Session) -> McpClient:
    client = McpClient(endpoint_mcp_url, mcp_token, http_session)
    client.initialize()
    return client


@pytest.fixture
def hub_mcp(hub_mcp_url: str, http_session: requests.Session) -> McpClient:
    """Anonymous: a hub is the open-data door over several spaces, and a token minted for
    another endpoint's audience is refused here, which is the gateway being right."""
    client = McpClient(hub_mcp_url, os.getenv("HUB_MCP_TOKEN"), http_session)
    client.initialize()
    return client


@pytest.fixture
def config_mcp(config_mcp_url: str, config_mcp_token: str | None, http_session: requests.Session) -> McpClient:
    client = McpClient(config_mcp_url, config_mcp_token, http_session)
    client.initialize()
    return client


@pytest.fixture
def portal_mcp(portal_mcp_url: str, config_mcp_token: str | None, http_session: requests.Session) -> McpClient:
    client = McpClient(portal_mcp_url, config_mcp_token, http_session)
    client.initialize()
    return client


@pytest.fixture
def agent_config_mcp(config_mcp_url: str, mcp_agent_token: str, http_session: requests.Session) -> McpClient:
    client = McpClient(config_mcp_url, mcp_agent_token, http_session)
    client.initialize()
    return client


@pytest.fixture
def other_space_mcp(other_space_mcp_url: str, mcp_token: str | None, http_session: requests.Session) -> McpClient:
    client = McpClient(other_space_mcp_url, mcp_token, http_session)
    client.initialize()
    return client


@pytest.fixture
def init_result(uninit_mcp: McpClient) -> dict:
    return uninit_mcp.initialize()

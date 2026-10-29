"""A personal connector's MCP server, `/c/{id}`, against MCP 2026-07-28 (T-5969, API/14 §3).

Each case cites the section of the MCP specification revision 2026-07-28 it checks
(https://modelcontextprotocol.io/specification/2026-07-28/...): `basic/authorization` (AUTH),
`basic/authorization/authorization-server-discovery` (DISCOVERY), `basic/transports/streamable-http`
(HTTP), `basic/lifecycle` (LIFECYCLE) and `server/discover` (SERVER-DISCOVER).

`CONNECTOR_URL` is one connector's address, `https://portal.{domain}/c/{id}`; without it the
module skips. The cases that need a token skip without one, so the anonymous half runs against
any instance: `CONNECTOR_TOKEN` is a token the person got for that connector,
`OTHER_CONNECTOR_URL` a second connector of the same Portal, and `CONNECTOR_FOREIGN_TOKEN` a
token the same realm issued for another resource (defaults to `MCP_TOKEN`, the data plane's).
No case prints a token.
"""

from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import urlsplit

import pytest
import requests
from conftest import _require_env

MODERN = "2026-07-28"
META = "io.modelcontextprotocol/"
PARAM = re.compile(r'([A-Za-z_]+)="([^"]*)"')


@pytest.fixture(scope="module")
def connector_url() -> str:
    return _require_env("CONNECTOR_URL").rstrip("/")


@pytest.fixture(scope="module")
def other_connector_url() -> str:
    return _require_env("OTHER_CONNECTOR_URL").rstrip("/")


@pytest.fixture(scope="module")
def connector_token() -> str:
    return _require_env("CONNECTOR_TOKEN")


@pytest.fixture(scope="module")
def foreign_token() -> str:
    token = os.getenv("CONNECTOR_FOREIGN_TOKEN") or os.getenv("MCP_TOKEN")
    if not token:
        pytest.skip("CONNECTOR_FOREIGN_TOKEN (or MCP_TOKEN) is not set — see tests/mcp/README.md")
    return token


def modern(method: str, params: dict | None = None, version: str = MODERN) -> tuple[dict, dict[str, str]]:
    """A 2026-07-28 request: version and capabilities in `_meta`, mirrored in the headers (HTTP
    "Request Metadata")."""
    params = dict(params or {})
    params["_meta"] = {
        META + "protocolVersion": version,
        META + "clientInfo": {"name": "jc-conformance", "version": "1.0.0"},
        META + "clientCapabilities": {},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": version,
        "Mcp-Method": method,
    }
    if "name" in params:
        headers["Mcp-Name"] = params["name"]
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers


def send(http: requests.Session, url: str, token: str | None, body: dict, headers: dict[str, str]) -> requests.Response:
    if token:
        headers = {**headers, "Authorization": f"Bearer {token}"}
    return http.post(url, json=body, headers=headers, timeout=30, allow_redirects=False)


def challenge(resp: requests.Response) -> dict[str, str]:
    value = resp.headers.get("WWW-Authenticate", "")
    assert value.startswith("Bearer"), f"AUTH Error Handling: {resp.status_code} carries no Bearer challenge: {value!r}"
    return dict(PARAM.findall(value))


def metadata(http: requests.Session, url: str) -> tuple[dict[str, str], dict]:
    """The anonymous challenge of `url` and the RFC 9728 document it names."""
    body, headers = modern("tools/list")
    resp = send(http, url, None, body, headers)
    assert resp.status_code == 401, (
        f"AUTH Error Handling: an anonymous call answered {resp.status_code} "
        f"{resp.headers.get('Location', '')!r} (a login redirect is no door for an MCP client)"
    )
    params = challenge(resp)
    assert "resource_metadata" in params, f"DISCOVERY (RFC 9728 §5.1): the 401 names no resource_metadata: {params}"
    doc = http.get(params["resource_metadata"], timeout=30, allow_redirects=False)
    assert doc.status_code == 200, f"RFC 9728 §3.2: the metadata answered {doc.status_code}"
    return params, doc.json()


def test_the_401_challenge_names_this_connector_its_metadata_and_scope(
    http_session: requests.Session, connector_url: str
):
    """DISCOVERY "Protected Resource Metadata Discovery" (WWW-Authenticate mechanism), AUTH "Scope
    Selection Strategy", AUTH "Canonical Server URI": the metadata's `resource` is this
    connector's address exactly, it names an authorization server, the challenge names the scope
    a client asks for, and a token goes only in the header."""
    params, doc = metadata(http_session, connector_url)
    assert doc.get("resource") == connector_url, (
        f"AUTH Canonical Server URI / RFC 9728 §3.3: resource {doc.get('resource')!r} is not {connector_url!r}"
    )
    assert doc.get("authorization_servers"), f"DISCOVERY: no authorization_servers: {doc}"
    assert params.get("scope", "").split(), f"AUTH Scope Selection Strategy: the 401 names no scope: {params}"
    assert doc.get("scopes_supported"), f"AUTH Scope Selection Strategy: no scopes_supported: {doc}"
    assert "query" not in doc.get("bearer_methods_supported", []), (
        f"AUTH Token Requirements 2: a token must never travel in the query string: {doc}"
    )


def test_two_connectors_are_two_resources_with_two_scopes(
    http_session: requests.Session, connector_url: str, other_connector_url: str
):
    """AUTH "Canonical Server URI", RFC 8707 §2: one token per connector, so each connector is its
    own resource and asks for a scope the other does not."""
    mine, my_doc = metadata(http_session, connector_url)
    theirs, their_doc = metadata(http_session, other_connector_url)
    assert my_doc.get("resource") != their_doc.get("resource"), (my_doc, their_doc)
    assert set(mine["scope"].split()).isdisjoint(theirs["scope"].split()), (
        f"a scope two connectors share opens both: {mine['scope']!r} / {theirs['scope']!r}"
    )


def test_the_authorization_server_sends_iss_and_takes_s256(http_session: requests.Session, connector_url: str):
    """AUTH "Authorization Response Validation" (RFC 9207 §2.3: a server that sends `iss` says
    so), DISCOVERY "Authorization Server Metadata Discovery" (the document's issuer is the one
    the URL was built from, else a client must not use it), AUTH PKCE (S256)."""
    _, doc = metadata(http_session, connector_url)
    for issuer in doc["authorization_servers"]:
        parts = urlsplit(issuer)
        origin, tail = f"{parts.scheme}://{parts.netloc}", parts.path.rstrip("/")
        candidates = [f"{origin}/.well-known/oauth-authorization-server{tail}",
                      f"{origin}/.well-known/openid-configuration{tail}",
                      f"{origin}{tail}/.well-known/openid-configuration"]
        found = None
        for url in candidates:
            resp = http_session.get(url, timeout=30, allow_redirects=False)
            if resp.status_code == 200:
                found = resp.json()
                break
        assert found is not None, f"DISCOVERY: no metadata for {issuer} at {candidates}"
        assert found.get("issuer") == issuer, f"DISCOVERY: issuer {found.get('issuer')!r} is not {issuer!r}"
        assert found.get("authorization_response_iss_parameter_supported") is True, (
            f"AUTH Authorization Response Validation (RFC 9207): {issuer} does not send iss"
        )
        assert "S256" in found.get("code_challenge_methods_supported", []), f"AUTH PKCE: {issuer} lacks S256"


def _unsigned_jwt() -> str:
    """An alg=none JWT, built here so no token-shaped literal sits in the source."""
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'scope': 'connector'})}."


@pytest.mark.parametrize("token", ["not-a-token", _unsigned_jwt()], ids=["garbage", "unsigned"])
def test_an_invalid_token_is_401_with_the_challenge(http_session: requests.Session, connector_url: str, token: str):
    """AUTH "Token Handling": an invalid token, an unsigned one included, is a 401 that still
    tells the client where to get a good one."""
    body, headers = modern("tools/list")
    resp = send(http_session, connector_url, token, body, headers)
    assert resp.status_code == 401, f"AUTH Token Handling: an invalid token answered {resp.status_code}"
    assert "resource_metadata" in challenge(resp)


def test_a_token_in_the_query_string_opens_nothing(
    http_session: requests.Session, connector_url: str, connector_token: str
):
    """AUTH "Token Requirements" 2: access tokens MUST NOT be included in the URI query string,
    so a server reads none from there."""
    body, headers = modern("tools/list")
    resp = http_session.post(f"{connector_url}?access_token={connector_token}", json=body, headers=headers,
                             timeout=30, allow_redirects=False)
    assert resp.status_code == 401, f"AUTH Token Requirements: a query-string token answered {resp.status_code}"


def test_a_token_for_another_resource_is_not_passed_through(
    http_session: requests.Session, connector_url: str, foreign_token: str
):
    """AUTH "Token Handling" and "Access Token Privilege Restriction": a server MUST validate that
    a token was issued for it and MUST NOT accept or transit any other token (no token
    passthrough). A token of the same realm for another resource is refused."""
    body, headers = modern("tools/list")
    resp = send(http_session, connector_url, foreign_token, body, headers)
    assert resp.status_code in (401, 403), f"AUTH Token Handling: a foreign token answered {resp.status_code}"
    assert "tools" not in resp.text, "a refused call listed tools"


def test_a_token_for_one_connector_is_refused_at_another_with_its_scope(
    http_session: requests.Session, connector_url: str, other_connector_url: str, connector_token: str
):
    """Audience confusion. AUTH "Scope Challenge Handling" / "Runtime Insufficient Scope Errors"
    (RFC 6750 §3.1): a valid token without the other connector's scope is refused, a 403 says
    `insufficient_scope`, and either refusal names the other connector's metadata and every scope
    a step-up needs, in one challenge."""
    theirs, _ = metadata(http_session, other_connector_url)
    body, headers = modern("tools/list")
    resp = send(http_session, other_connector_url, connector_token, body, headers)
    assert resp.status_code in (401, 403), f"connector A's token answered {resp.status_code} at connector B"
    params = challenge(resp)
    if resp.status_code == 403:
        assert params.get("error") == "insufficient_scope", f"AUTH Runtime Insufficient Scope Errors: {params}"
    assert params.get("resource_metadata") == theirs["resource_metadata"], params
    assert set(theirs["scope"].split()) <= set(params.get("scope", "").split()), (
        f"AUTH Scope Challenge Handling: the refusal does not name B's scope {theirs['scope']!r}: {params}"
    )
    assert "tools" not in resp.text, "a refused call listed tools"


def test_a_modern_request_is_served_without_a_handshake(
    http_session: requests.Session, connector_url: str, connector_token: str
):
    """LIFECYCLE "Protocol Version Negotiation" (a request carrying modern `_meta` is served
    statelessly): `tools/list` with no `initialize` and no session answers, twice alike."""
    body, headers = modern("tools/list")
    first = send(http_session, connector_url, connector_token, body, headers)
    second = send(http_session, connector_url, connector_token, body, headers)
    for resp in (first, second):
        assert resp.status_code == 200, f"LIFECYCLE: a stateless tools/list answered {resp.status_code}: {resp.text[:300]}"
        assert isinstance(resp.json().get("result", {}).get("tools"), list), resp.text[:300]
    assert first.json()["result"] == second.json()["result"], "two stateless calls answered differently"


def test_server_discover_lists_the_supported_versions(
    http_session: requests.Session, connector_url: str, connector_token: str
):
    """SERVER-DISCOVER: servers MUST implement `server/discover`; it names `supportedVersions`."""
    body, headers = modern("server/discover")
    resp = send(http_session, connector_url, connector_token, body, headers)
    assert resp.status_code == 200, f"SERVER-DISCOVER answered {resp.status_code}: {resp.text[:300]}"
    assert MODERN in resp.json().get("result", {}).get("supportedVersions", []), resp.text[:300]


@pytest.mark.parametrize(
    "change",
    [
        {"Mcp-Method": "tools/call"},
        {"MCP-Protocol-Version": "2025-11-25"},
        {"Mcp-Method": None},
    ],
    ids=["method-differs", "version-differs", "method-missing"],
)
def test_a_header_that_disagrees_with_the_body_is_a_header_mismatch(
    http_session: requests.Session, connector_url: str, connector_token: str, change: dict
):
    """HTTP "Server Validation": a missing standard header or one that differs from the body is
    400 with JSON-RPC error -32020 (HeaderMismatch), so a router reading the header and the
    server reading the body never act on two different requests."""
    body, headers = modern("tools/list")
    headers = {k: v for k, v in {**headers, **change}.items() if v is not None}
    resp = send(http_session, connector_url, connector_token, body, headers)
    assert resp.status_code == 400, f"HTTP Server Validation: {change} answered {resp.status_code}"
    assert resp.json().get("error", {}).get("code") == -32020, resp.text[:300]


def test_an_unsupported_version_names_the_supported_ones(
    http_session: requests.Session, connector_url: str, connector_token: str
):
    """HTTP "Protocol Version Header", LIFECYCLE "Protocol Version Negotiation": an unknown
    version is 400 with UnsupportedProtocolVersionError (-32022) listing what the server speaks."""
    body, headers = modern("tools/list", version="1900-01-01")
    resp = send(http_session, connector_url, connector_token, body, headers)
    assert resp.status_code == 400, f"HTTP Protocol Version Header: 1900-01-01 answered {resp.status_code}"
    error = resp.json().get("error", {})
    assert error.get("code") == -32022, resp.text[:300]
    assert MODERN in error.get("data", {}).get("supported", []), resp.text[:300]

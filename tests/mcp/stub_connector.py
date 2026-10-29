"""The stub's personal connector door, `/c/{id}` (T-5969, API/14 §3, MCP 2026-07-28).

Conforming mode answers as the Portal's door should: RFC 9728 metadata naming the connector,
a 401 challenge naming that metadata and the connector's scope, a 403 `insufficient_scope` to a
token scoped to another connector, a 403 to a token a client the connector does not allow obtained, a 401 to a token issued for another resource, and the
2026-07-28 stateless transport (per-request `_meta`, `MCP-Protocol-Version` / `Mcp-Method` /
`Mcp-Name` checked against the body, `server/discover`). Broken mode carries one defect per
case of `test_connector_door.py`, so each case can be seen going red (selftest.py).
"""

from __future__ import annotations

import json
import re

#: Connector id -> the one token scoped to it (`connector:{id}`).
CONNECTORS = {
    "cstubaaaaaaaaaaaaaaaaaaaaa": "connector-a-token",
    "cstubbbbbbbbbbbbbbbbbbbbbb": "connector-b-token",
}
#: Connector A answers only claude.ai (allowed clients, T-6024): a token for A that Claude Code
#: obtained is the owner's and scoped right, and still refused for its client.
UNLISTED_CLIENT_TOKEN = "connector-a-claude-code-token"
#: A token the same realm issued for another resource (the data plane's `MCP_TOKEN`).
FOREIGN_TOKEN = "viewer-token"
VERSIONS = ["2026-07-28", "2025-11-25"]
META = "io.modelcontextprotocol/"
PATH = re.compile(r"^/c/([a-z2-7]{26})(/\.well-known/oauth-protected-resource)?$")


def _host(handler) -> str:
    return f"http://{handler.headers.get('Host') or f'127.0.0.1:{handler.server.server_address[1]}'}"


def _json(handler, status: int, body: dict, headers: dict[str, str] | None = None) -> None:
    handler._send_bytes(status, "application/json", json.dumps(body).encode("utf-8"), headers=headers)


def _challenge(handler, cid: str, error: str | None = None) -> dict[str, str]:
    parts = [f'resource_metadata="{_host(handler)}/c/{cid}/.well-known/oauth-protected-resource"',
             f'scope="connector:{cid}"']
    if error:
        parts.insert(0, f'error="{error}"')
    return {"WWW-Authenticate": "Bearer " + ", ".join(parts)}


def handle_get(handler, path: str) -> bool:
    broken = handler.server.mode == "broken"
    if path == "/.well-known/oauth-authorization-server/oauth":
        _json(handler, 200, {
            "issuer": f"{_host(handler)}/oauth",
            "authorization_endpoint": f"{_host(handler)}/oauth/auth",
            "token_endpoint": f"{_host(handler)}/oauth/token",
            "code_challenge_methods_supported": ["S256"],
            # Defect (broken mode): no RFC 9207 `iss` in authorization responses.
            "authorization_response_iss_parameter_supported": not broken,
        })
        return True
    match = PATH.match(path)
    if not match or not match.group(2):
        return False
    cid = match.group(1)
    _json(handler, 200, {
        # Defect (broken mode): the document names the host, not the connector.
        "resource": _host(handler) if broken else f"{_host(handler)}/c/{cid}",
        "authorization_servers": [f"{_host(handler)}/oauth"],
        "scopes_supported": [f"connector:{cid}"],
        "bearer_methods_supported": ["header"],
    })
    return True


def handle_post(handler, path: str, raw_body: bytes) -> bool:
    match = PATH.match(path)
    if not match or match.group(2):
        return False
    cid, broken = match.group(1), handler.server.mode == "broken"
    auth = handler.headers.get("Authorization", "")
    token = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    known = set(CONNECTORS.values()) | {FOREIGN_TOKEN, UNLISTED_CLIENT_TOKEN}
    if token not in known or (token == FOREIGN_TOKEN and not broken):
        # Defect (broken mode): a token issued for another resource passes through.
        _json(handler, 401, {"title": "Unauthorized"}, _challenge(handler, cid))
        return True
    if token in CONNECTORS.values() and token != CONNECTORS.get(cid) and not broken:
        # Defect (broken mode): any connector's token opens every connector.
        _json(handler, 403, {"title": "Forbidden"}, _challenge(handler, cid, "insufficient_scope"))
        return True
    if token == UNLISTED_CLIENT_TOKEN and not broken:
        # Defect (broken mode): the connector's allowed clients are never compared with the token's.
        _json(handler, 403, {"title": "Forbidden", "detail": "this connector answers only "
                             "https://claude.ai/oauth/mcp-client-metadata; the token was obtained by "
                             "https://claude.ai/oauth/claude-code-client-metadata"})
        return True
    try:
        req = json.loads(raw_body)
    except ValueError:
        _json(handler, 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        return True
    req_id, method = req.get("id"), req.get("method", "")
    params = req.get("params") or {}
    meta = params.get("_meta") or {}
    asked = meta.get(META + "protocolVersion")
    if asked is not None and asked not in VERSIONS:
        _json(handler, 400, {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32022, "message": "Unsupported protocol version",
            "data": {"supported": VERSIONS, "requested": asked}}})
        return True
    expected = {"MCP-Protocol-Version": asked, "Mcp-Method": method}
    if method in ("tools/call", "resources/read", "prompts/get"):
        expected["Mcp-Name"] = params.get("name") or params.get("uri")
    wrong = [name for name, value in expected.items() if handler.headers.get(name) != value]
    if wrong and not broken:
        # Defect (broken mode): the headers are never compared with the body.
        _json(handler, 400, {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32020, "message": f"Header mismatch: {', '.join(wrong)}"}})
        return True
    if method == "server/discover":
        result = {"resultType": "complete", "supportedVersions": VERSIONS, "capabilities": {"tools": {}},
                  "_meta": {META + "serverInfo": {"name": "stub-connector", "version": "1"}}}
    elif method == "tools/list":
        result = {"tools": [{"name": "jc_resource_list", "inputSchema": {"type": "object"},
                             "annotations": {"readOnlyHint": True}}]}
    else:
        _json(handler, 404, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}})
        return True
    _json(handler, 200, {"jsonrpc": "2.0", "id": req_id, "result": result})
    return True

"""A two-project platform in one process, with planted isolation defects (T-1708).

`test_tenant_isolation.py` plays one attack against every door a running platform opens. A suite
that only ever skips proves nothing, and a suite nobody has seen go red proves less: this stub is
the platform the suite is held against — correct in `isolating` mode, and wrong in one specific,
named way in each of the others.

The doors are the ones the suite knocks on: the Portal's collections, one resource by name, a
write, the project list, the changes, the activity and the export, an endpoint of the other
project, MCP, an artifact, the forge and an agent run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

PROJECT_A = "banskabystrica"
PROJECT_B = "zvolen"
TOKEN_A = "token-of-banskabystrica"
TOKEN_B = "token-of-zvolen"
SLUG_B = "zt4qm7ge2xdv6ksb3ncf5arw2y"
RUN_B = "run-7a1c"
ARTIFACT_B = "sha256-9f1c2b"
REPO_B = "zvolen/zvolen-config"
SLUG_A = "bb4qm7ge2xdv6ksb3ncf5arw2y"
SPACE_B = "voda"
TYPE_B = "WaterQualityObserved"
ENTITY_B = f"urn:ngsi-ld:{TYPE_B}:zvolen.sk:voda:w-9"
SERVER_B = "voda-mcp"
DATAMODEL_B = "voda"
#: What a leaky surface hands A: B's own entity.
LEAKED = {"id": ENTITY_B, "type": TYPE_B}

#: What each project holds, by collection.
HELD: dict[str, dict[str, list[str]]] = {
    PROJECT_A: {"spaces": ["ovzdusie"], "endpoints": ["verejne-ovzdusie"]},
    PROJECT_B: {"spaces": ["voda"], "endpoints": ["verejna-voda"]},
}

MODES = (
    "isolating",
    # The collection of the other project answers 200 with its contents.
    "leaky_list",
    # A name that exists answers 403 where an unknown name answers 404, so the refusal is a
    # directory of the other project.
    "existence_disclosure",
    # The write is refused to the caller and stored anyway.
    "write_lands",
    # The artifact store serves another project's bytes to whoever asks.
    "artifact_served",
    # MCP honours a `project` argument and answers the other organisation's entities.
    "mcp_follows_argument",
    # The temporal surface of B's Endpoint answers A with B's history.
    "leaky_temporal",
    # B's Endpoint WebSocket opens for A and pushes B's entity.
    "leaky_ws",
    # The MCP hub resolves B's slug for A and answers B's record.
    "leaky_hub_mcp",
    # Nothing strips `x-jc-*`, `jc-provenance-*` or `l5d-*`, and B's doors believe them (T-5566).
    "trusts_identity_headers",
)

#: The header families only the platform's own services write (T-5566).
IDENTITY_PREFIXES = ("x-jc-", "jc-provenance-", "l5d-")


@dataclass
class State:
    mode: str
    written: dict[str, list[str]] = field(default_factory=dict)


def _project_of(token: str | None) -> str | None:
    return {f"Bearer {TOKEN_A}": PROJECT_A, f"Bearer {TOKEN_B}": PROJECT_B}.get(token or "")


def _manifest(project: str, kind: str, name: str) -> dict[str, Any]:
    return {
        "apiVersion": "joinedcontext.com/v1alpha1",
        "kind": kind,
        "metadata": {"name": name, "namespace": project},
        "spec": {},
    }


class Handler(BaseHTTPRequestHandler):
    state: State

    def log_message(self, *_args: Any) -> None:  # noqa: D102 - quiet in the test output
        return

    # -- plumbing ---------------------------------------------------------------------------
    def _send(self, status: int, body: Any = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _not_found(self) -> None:
        # One body for every refusal, so a caller learns nothing from the difference (R20).
        self._send(404, {"type": "urn:joinedcontext:problem:not-found", "title": "Not Found", "status": 404})

    def _websocket(self, text: str) -> None:
        """Accepts the upgrade (RFC 6455 4.2.2) and sends one unmasked text frame."""
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept.decode())
        self.end_headers()
        payload = text.encode()
        self.wfile.write(bytes([0x81, len(payload)]) + payload)
        self.wfile.flush()
        self.close_connection = True

    def _believes_forged_identity(self, path: str) -> bool:
        """B's doors, in `trusts_identity_headers` mode, when the request carries a forged identity."""
        forged = any(name.lower().startswith(IDENTITY_PREFIXES) for name in self.headers)
        names_b = SLUG_B in path or SPACE_B in path or SERVER_B in path
        return self.state.mode == "trusts_identity_headers" and forged and names_b

    def _caller(self) -> str | None:
        return _project_of(self.headers.get("Authorization"))

    def _body(self) -> dict[str, Any]:
        """The request's JSON body, read once and kept, so two doors may look at it."""
        if not hasattr(self, "_read"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                self._read = json.loads(self.rfile.read(length)) if length else {}
            except json.JSONDecodeError:
                self._read = {}
        return self._read

    # -- the doors --------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        if self.headers.get("Upgrade", "").lower() == "websocket":
            # A WebSocket client takes only an HTTP/1.1 answer to its handshake, a refusal too
            # (RFC 6455 4.1); one answer per connection, as on every other door here.
            self.protocol_version = "HTTP/1.1"
            self.close_connection = True
        self._get()
        if self.protocol_version == "HTTP/1.1":
            self.close_connection = True

    def _get(self) -> None:
        path = urlparse(self.path).path
        caller = self._caller()
        parts = [part for part in path.split("/") if part]

        if path == "/api/v1/projects":
            if not caller:
                return self._send(401, {"title": "Unauthorized", "status": 401})
            return self._send(200, {"items": [{"name": caller}]})

        if parts[:3] == ["api", "v1", "projects"] and len(parts) >= 5:
            project, plural = parts[3], parts[4]
            mine = caller is not None and project == caller
            known = project in HELD

            if len(parts) == 5:
                if plural in {"changes", "activity", "export"}:
                    return self._send(200, {"items": []}) if mine else self._not_found()
                if self.state.mode == "leaky_list" and known and not mine:
                    return self._send(
                        200,
                        {"items": [_manifest(project, "ContextSpace", name) for name in HELD[project].get("spaces", [])]},
                    )
                if not mine:
                    return self._not_found()
                names = list(HELD[project].get(plural, [])) + self.state.written.get(f"{project}/{plural}", [])
                return self._send(200, {"items": [_manifest(project, "ContextSpace", name) for name in names]})

            if plural == "agent-runs":
                return self._not_found() if not mine else self._send(200, {"items": []})

            name = parts[5]
            exists = name in HELD.get(project, {}).get(plural, []) or name in self.state.written.get(
                f"{project}/{plural}", []
            )
            if mine:
                return self._send(200, _manifest(project, "ContextSpace", name)) if exists else self._not_found()
            if self.state.mode == "existence_disclosure" and exists:
                return self._send(403, {"title": "Forbidden", "status": 403})
            return self._not_found()

        if path == "/api/endpoints":
            # The hub's listing holds what the caller reaches: A's Endpoint only.
            return self._send(200, {"items": [{"slug": SLUG_A}]} if caller else {"items": []})

        if path == "/catalog.jsonld":
            return self._send(200, {"@graph": [{"@id": f"/api/endpoint/{SLUG_A}"}]})

        if self._believes_forged_identity(path):
            if self.headers.get("Upgrade", "").lower() == "websocket":
                return self._websocket(json.dumps(LEAKED))
            return self._send(200, [LEAKED])

        if self.state.mode == "leaky_temporal" and path.startswith(f"/api/endpoint/{SLUG_B}/ngsi-ld/v1/temporal/"):
            return self._send(200, [LEAKED])

        if self.state.mode == "leaky_ws" and path == f"/api/endpoint/{SLUG_B}/ws":
            return self._websocket(json.dumps(LEAKED))

        if "/api/endpoint/" in path:
            # A slug of the other project and a slug that is nothing at all answer the same: the
            # gateway does not say which of the two a caller has found (EP-02, R20).
            return self._not_found()

        if path.startswith("/artifacts/"):
            if self.state.mode == "artifact_served":
                return self._send(200, {"bytes": "x" * 8192})
            return self._send(403, {"title": "Forbidden", "status": 403})

        if path.startswith("/forge/api/v1/repos/search"):
            return self._send(200, {"data": []})

        if path.startswith("/forge/api/v1/repos/"):
            return self._not_found()

        return self._not_found()

    def do_POST(self) -> None:  # noqa: N802
        self.__dict__.pop("_read", None)  # a kept-alive connection carries the next request
        path = urlparse(self.path).path
        caller = self._caller()
        parts = [part for part in path.split("/") if part]

        if self._believes_forged_identity(path) or (
            path == "/api/mcp" and self._believes_forged_identity(json.dumps(self._body()))
        ):
            return self._send(200, {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(LEAKED)}]}})

        if path == "/api/mcp":
            # The hub answers a slug the caller does not reach as it answers one nobody created.
            asked = ((self._body().get("params") or {}).get("arguments") or {}).get("endpoint")
            if self.state.mode == "leaky_hub_mcp" and asked == SLUG_B:
                text = json.dumps(LEAKED)
            else:
                text = f"no endpoint {asked} you can read"
            return self._send(
                200, {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [{"type": "text", "text": text}]}}
            )

        if path.rstrip("/").endswith("/mcp"):
            asked = (self._body().get("params") or {}).get("arguments") or {}
            entity = f"urn:ngsi-ld:AirQualityObserved:banskabystrica.sk:{PROJECT_A}:s-1"
            if self.state.mode == "mcp_follows_argument" and asked.get("project") == PROJECT_B:
                entity = f"urn:ngsi-ld:AirQualityObserved:zvolen.sk:{PROJECT_B}:s-9"
            return self._send(200, {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": entity}]}})

        if parts[:3] == ["api", "v1", "projects"] and len(parts) == 5:
            project, plural = parts[3], parts[4]
            body = self._body()
            name = (body.get("metadata") or {}).get("name", "")
            if caller is not None and project == caller:
                self.state.written.setdefault(f"{project}/{plural}", []).append(name)
                return self._send(201, body)
            if self.state.mode == "write_lands" and name:
                self.state.written.setdefault(f"{project}/{plural}", []).append(name)
            return self._not_found()

        return self._not_found()

    def do_PATCH(self) -> None:  # noqa: N802
        self.__dict__.pop("_read", None)  # a kept-alive connection carries the next request
        self._body()
        return self._not_found()

    def do_DELETE(self) -> None:  # noqa: N802
        return self._not_found()


@dataclass
class Stub:
    url: str
    server: HTTPServer
    thread: threading.Thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def serve(mode: str = "isolating", port: int = 0) -> Stub:
    """Starts the stub on `port` (0 picks a free one) and returns it with its base URL."""
    if mode not in MODES:
        raise ValueError(f"{mode} is not one of {MODES}")
    handler = type("BoundHandler", (Handler,), {"state": State(mode=mode)})
    server = HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, bound = server.server_address[0], server.server_address[1]
    return Stub(url=f"http://{host}:{bound}", server=server, thread=thread)

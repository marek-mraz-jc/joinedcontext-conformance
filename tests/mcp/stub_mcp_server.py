"""Stub Model Context Protocol (MCP) server for conformance testing (T-0060, T-0061).

Provides an in-process, multi-surface Streamable HTTP MCP server covering:
- Per-space data MCP surfaces (/cs/ovzdusie/mcp, /cs/doprava/mcp)
- Private space with RFC 9728 authentication (/cs/tajne/mcp)
- Endpoint MCP surface (/api/endpoint/aq-public/mcp)
- Hub MCP surface, a space of registrations over two others (/cs/hub/mcp)
- Configuration MCP surface (/config/mcp)
- RFC 9728 protected resource metadata (/.well-known/oauth-protected-resource/cs/tajne/mcp)

Operates in two modes:
- `conforming` (default): strictly satisfies MCP Streamable HTTP, JSON-RPC 2.0,
  tool behavior annotations, context space tenant isolation, and agent governance invariants.
- `broken`: intentionally introduces defects to verify the test suite assertions can go red.

| # | Broken Mode Defect | Targeted Assertion / Requirement |
|---|---|---|
| 1 | `jsonrpc: "1.0"` in every response frame | SP-14 framing (`test_sp14_jsonrpc_response_framing`) |
| 2 | Omit `annotations` on all tools | AG-07 tool hints (`test_ag07_tool_behavior_annotations`) |
| 3 | `query_entities` honours tool `space`/`tenant` arg | AG-05, SP-14 selector isolation (`test_ag05_sp14_a_space_argument_never_moves_the_caller`) |
| 4 | `get_entity` answers "entity exists in another space" | SP-20 byte-identical error (`test_sp20_a_cross_space_probe_is_byte_identical_to_a_miss`) |
| 5 | Configuration MCP applies changes directly (`applied: true`) | AG-06 non-mutating invariant (`test_ag06_the_configuration_mcp_writes_nothing_directly`) |
| 6 | Agent token can approve MR and set risk class | AG-11 self-approval and lane immutability (`test_ag11_an_agent_neither_approves_itself_nor_edits_the_lanes`) |
| 7 | Private space answers 200 without 401 or RFC 9728 auth | SP-17 private MCP authentication (`test_sp17_private_mcp_requires_rfc9728_auth`) |
| 8 | `query_entities` returns `secretReading` without `restricted` | AG-13 policy-narrowed result grounding (`test_ag13_a_narrowed_answer_says_so_and_leaks_nothing`) |
| 9 | Unknown `Mcp-Session-Id` accepted without HTTP 404 | SP-20 session lifecycle (`test_sp20_session_lifecycle_and_protocol_version_header`) |
| 10 | Tool arguments are passed on without validating them against the published schema | AG-21 parameter validation (`test_ag21_live_mcp_rejects_hostile_tool_parameters`, security suite) |
| 11 | The hub surface announces nothing, answers without `jc:source` and offers its write tools | EP-71/PF-48 federation grounding (`test_ep71_*`, `test_pf48_*`) |
"""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jsonschema

ENTITIES_BY_SPACE = {
    "ovzdusie": [
        {
            "id": "urn:ngsi-ld:Device:joinedcontext.com:ovzdusie:dev-1",
            "type": "AirQualityObserved",
            "temperature": 21.5,
            "secretReading": "classified-sensor-readout-1",
        },
        {
            "id": "urn:ngsi-ld:Device:joinedcontext.com:ovzdusie:dev-2",
            "type": "AirQualityObserved",
            "temperature": 22.0,
            "secretReading": "classified-sensor-readout-2",
        },
    ],
    "doprava": [
        {
            "id": "urn:ngsi-ld:Device:joinedcontext.com:doprava:probe-1",
            "type": "Device",
            "speed": 50,
            "secretReading": "traffic-secret-1",
        },
        {
            "id": "urn:ngsi-ld:Device:joinedcontext.com:doprava:probe-2",
            "type": "Device",
            "speed": 60,
            "secretReading": "traffic-secret-2",
        },
    ],
    "tajne": [
        {
            "id": "urn:ngsi-ld:Device:joinedcontext.com:tajne:dev-1",
            "type": "Device",
            "secretReading": "classified-private-readout",
        }
    ],
}


class StubMcpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - silence standard library access log
        pass

    def _send_bytes(self, status: int, content_type: str, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json_rpc(self, req_id: int | str | None, result: dict | None = None, error: dict | None = None, headers: dict[str, str] | None = None) -> None:
        jsonrpc_ver = "1.0" if self.server.mode == "broken" else "2.0"
        frame: dict = {"jsonrpc": jsonrpc_ver, "id": req_id}
        if error is not None:
            frame["error"] = error
        else:
            frame["result"] = result if result is not None else {}
        body = json.dumps(frame).encode("utf-8")
        self._send_bytes(200, "application/json", body, headers=headers)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/.well-known/oauth-protected-resource/cs/tajne/mcp":
            host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
            metadata = {
                "resource": f"http://{host}/cs/tajne/mcp",
                "authorization_servers": [f"http://{host}/oauth/token"],
            }
            body = json.dumps(metadata).encode("utf-8")
            self._send_bytes(200, "application/json", body)
            return

        self._send_bytes(404, "application/json", b'{"error":"not found"}')

    # The hub surface (T-1209): a space that holds only registrations, over two others. What a
    # model cannot work out for itself is that the answer is a union, over which sources, and
    # that it can be partial — so the stub says exactly that, by name and never by address.
    HUB_MEMBERS = ["indicators", "transport"]
    HUB_INSTRUCTIONS = (
        "Every tool of this server reads the one context space behind this URL. "
        "This space federates indicators, transport: every read answers over their union, "
        "a result carries the names it came from as `jc:source`, and an answer can be "
        "partial when one of them does not respond."
    )

    def _is_hub(self, path: str) -> bool:
        return path.endswith("/cs/hub/mcp")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        # read the body before any early return: an unread body desynchronizes the keep-alive
        # connection and the next request on it comes back as a 400 from http.server itself
        raw_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        # SP-17: Private MCP surface authentication check
        if path == "/cs/tajne/mcp" and self.server.mode != "broken":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
                meta_url = f"http://{host}/.well-known/oauth-protected-resource/cs/tajne/mcp"
                headers = {"WWW-Authenticate": f'Bearer error="unauthorized", resource_metadata="{meta_url}"'}
                self._send_bytes(401, "application/json", b'{"error":"unauthorized"}', headers=headers)
                return

        # SP-20: Mcp-Session-Id validation
        session_id_header = self.headers.get("Mcp-Session-Id")
        if session_id_header is not None:
            if self.server.mode != "broken" and session_id_header not in self.server.sessions:
                self._send_bytes(404, "application/json", b'{"error":"unknown session"}')
                return
            # Missing protocol version after initial handshake returns 400
            proto_version = self.headers.get("MCP-Protocol-Version")
            if not proto_version:
                self._send_bytes(400, "application/json", b'{"error":"missing MCP-Protocol-Version"}')
                return

        try:
            req = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self._send_json_rpc(None, error={"code": -32700, "message": "Parse error"})
            return

        if not isinstance(req, dict):
            self._send_json_rpc(None, error={"code": -32600, "message": "Invalid Request"})
            return

        req_id = req.get("id")
        method = req.get("method")

        # SP-14: Notification request handling (no id member)
        if req_id is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if req.get("jsonrpc") != "2.0":
            self._send_json_rpc(req_id, error={"code": -32600, "message": "Invalid Request"})
            return

        # Initialize method
        if method == "initialize":
            params = req.get("params") or {}
            client_version = params.get("protocolVersion", "2026-07-28")
            negotiated_ver = client_version if client_version <= "2026-07-28" else "2026-07-28"
            new_session_id = str(uuid.uuid4())
            self.server.sessions.add(new_session_id)

            result = {
                "protocolVersion": negotiated_ver,
                "serverInfo": {"name": "stub-mcp-server", "version": "1.0.0"},
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": False, "listChanged": False},
                },
            }
            if self._is_hub(path):
                # Defect 11: a hub that does not say what it federates, so a model reads a union
                # as if it were one space (EP-71, T-1209).
                if self.server.mode != "broken":
                    result["instructions"] = self.HUB_INSTRUCTIONS
            self._send_json_rpc(req_id, result=result, headers={"Mcp-Session-Id": new_session_id})
            return

        if method == "ping":
            self._send_json_rpc(req_id, result={})
            return

        if method == "resources/list":
            result = {
                "resources": [
                    {"uri": "schema://ovzdusie/v1/model.json", "name": "ovzdusie-schema"}
                ]
            }
            self._send_json_rpc(req_id, result=result)
            return

        is_config_plane = path == "/config/mcp"

        if method == "tools/list":
            tools = self._list_tools(is_config_plane)
            if self._is_hub(path) and self.server.mode != "broken":
                # A hub over registrations is a read: the write tools of an ordinary space
                # have nothing to write to here (PF-48).
                tools = [
                    tool for tool in tools
                    if tool.get("annotations", {}).get("readOnlyHint") is True
                ]
            self._send_json_rpc(req_id, result={"tools": tools})
            return

        if method == "tools/call":
            params = req.get("params") or {}
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            invalid = self._schema_violation(tool_name, tool_args, is_config_plane)
            if invalid and self.server.mode != "broken":
                # AG-21: parameters are validated against the published draft-07 schema before
                # anything downstream sees them. Defect 10 (broken mode) skips this on purpose.
                self._send_json_rpc(req_id, error={"code": -32602, "message": invalid})
                return
            self._call_tool(req_id, tool_name, tool_args, is_config_plane, path)
            return

        self._send_json_rpc(req_id, error={"code": -32601, "message": f"Method not found: {method}"})

    def _schema_violation(self, tool_name: str, arguments: dict, is_config_plane: bool) -> str | None:
        """The published inputSchema of the tool, applied to the arguments (AG-21).

        Returns the message of the first violation, or None when the arguments are valid.
        Properties the schema does not declare are left alone: AG-05 requires a `space` or
        `tenant` argument to be ignored, and ignoring it means accepting the call.
        """
        for tool in self._list_tools(is_config_plane):
            if tool["name"] != tool_name:
                continue
            schema = dict(tool.get("inputSchema") or {})
            try:
                errors = sorted(
                    jsonschema.Draft7Validator(schema).iter_errors(arguments),
                    key=lambda e: list(e.absolute_path),
                )
            except jsonschema.SchemaError as broken_schema:  # a fixture bug, not a caller bug
                return f"the published schema of {tool_name} is not valid draft-07: {broken_schema.message}"
            if errors:
                first = errors[0]
                where = ".".join(str(part) for part in first.absolute_path) or "(root)"
                return f"invalid parameter {where} for tool {tool_name}: {first.message}"
            return None
        return None

    def _list_tools(self, is_config_plane: bool) -> list[dict]:
        include_annotations = self.server.mode != "broken"

        if is_config_plane:
            tools = [
                {
                    "name": "propose_change",
                    "description": "Propose manifest changes via merge request",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                            "message": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                    "annotations": {"readOnlyHint": False, "destructiveHint": False},
                },
                {
                    "name": "approve_merge_request",
                    "description": "Approve a pending merge request",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                    "annotations": {"readOnlyHint": False, "destructiveHint": True},
                },
                {
                    "name": "set_risk_class",
                    "description": "Set risk classification for blueprint",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "blueprint": {"type": "string"},
                            "riskClass": {"type": "string"},
                        },
                        "required": ["blueprint", "riskClass"],
                    },
                    "annotations": {"readOnlyHint": False, "destructiveHint": True},
                },
            ]
        else:
            tools = [
                {
                    "name": "query_entities",
                    "description": "Query NGSI-LD entities",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "maxLength": 256, "pattern": "^[A-Za-z][A-Za-z0-9_-]*$"},
                            "q": {"type": "string", "maxLength": 4096},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                        },
                    },
                    "annotations": {"readOnlyHint": True, "destructiveHint": False},
                },
                {
                    "name": "get_entity",
                    "description": "Retrieve an entity by exact URN",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "attrs": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id"],
                    },
                    "annotations": {"readOnlyHint": True, "destructiveHint": False},
                },
                {
                    "name": "describe_schema",
                    "description": "Inspect DataModel in rendered formalism",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "entityType": {"type": "string"},
                            "format": {"type": "string"},
                        },
                    },
                    "annotations": {"readOnlyHint": True, "destructiveHint": False},
                },
                {
                    "name": "delete_entity",
                    "description": "Delete an entity instance",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                    },
                    "annotations": {"readOnlyHint": False, "destructiveHint": True},
                },
            ]

        if not include_annotations:
            for tool in tools:
                tool.pop("annotations", None)

        return tools

    def _call_tool(self, req_id: int | str, tool_name: str, tool_args: dict, is_config_plane: bool, path: str) -> None:
        if is_config_plane:
            if tool_name == "propose_change":
                if self.server.mode == "broken":
                    result = {"structuredContent": {"applied": True, "state": "applied"}}
                else:
                    result = {
                        "structuredContent": {
                            "mergeRequest": "https://git.example.com/joinedcontext/city/pulls/7",
                            "mergeRequestUrl": "https://git.example.com/joinedcontext/city/pulls/7",
                            "branch": "conformance/probe-branch",
                            "commit": "a1b2c3d4",
                            "applied": False,
                        }
                    }
                self._send_json_rpc(req_id, result=result)
                return

            if tool_name in ("approve_merge_request", "set_risk_class"):
                auth = self.headers.get("Authorization", "")
                token = auth.split("Bearer ", 1)[1].strip() if "Bearer " in auth else ""
                if self.server.mode != "broken" and token == self.server.agent_token:
                    result = {
                        "isError": True,
                        "content": [
                            {"type": "text", "text": "AG-11: agent identities cannot approve merge requests or edit approval lanes"}
                        ],
                    }
                else:
                    result = {"structuredContent": {"success": True}}
                self._send_json_rpc(req_id, result=result)
                return

            self._send_json_rpc(req_id, error={"code": -32601, "message": f"Tool not found: {tool_name}"})
            return

        # Data plane tools
        space = "ovzdusie"
        segments = path.split("/")
        if "cs" in segments[:-1]:
            space = segments[segments.index("cs") + 1]

        # AG-05 / Defect 3: Honouring space/tenant tool arguments in broken mode
        if self.server.mode == "broken":
            if "space" in tool_args:
                space = tool_args["space"]
            elif "tenant" in tool_args:
                space = tool_args["tenant"]

        if tool_name == "query_entities":
            entities = ENTITIES_BY_SPACE.get(space, [])
            if self._is_hub(path):
                # The union, and the names it is a union over. In broken mode the names are
                # missing, so a model cannot attribute what it was told (EP-71).
                merged = []
                for member_space in ("ovzdusie", "doprava"):
                    for entity in ENTITIES_BY_SPACE.get(member_space, []):
                        copy_e = dict(entity)
                        copy_e.pop("secretReading", None)
                        merged.append(copy_e)
                structured = {"entities": merged}
                if self.server.mode != "broken":
                    structured["jc:source"] = list(self.HUB_MEMBERS)
                self._send_json_rpc(req_id, result={"structuredContent": structured})
                return
            if self.server.mode == "broken":
                # AG-13 / Defect 8: Return secretReading and omit restricted: true
                result = {"structuredContent": {"entities": entities}}
            else:
                sanitized = []
                for e in entities:
                    copy_e = dict(e)
                    copy_e.pop("secretReading", None)
                    sanitized.append(copy_e)
                result = {
                    "structuredContent": {
                        "entities": sanitized,
                        "restricted": True,
                    }
                }
            self._send_json_rpc(req_id, result=result)
            return

        if tool_name == "get_entity":
            target_id = tool_args.get("id", "")
            current_space_entities = ENTITIES_BY_SPACE.get(space, [])
            found = next((e for e in current_space_entities if e["id"] == target_id), None)
            if found:
                result = {"structuredContent": found}
                self._send_json_rpc(req_id, result=result)
                return

            # SP-20 / Defect 4: Leaking foreign space existence in broken mode
            if self.server.mode == "broken":
                exists_elsewhere = any(
                    target_id in [e["id"] for e in elist]
                    for s, elist in ENTITIES_BY_SPACE.items()
                    if s != space
                )
                if exists_elsewhere:
                    result = {
                        "isError": True,
                        "content": [{"type": "text", "text": "entity exists in another space"}],
                    }
                    self._send_json_rpc(req_id, result=result)
                    return

            result = {
                "isError": True,
                "content": [{"type": "text", "text": f"Entity not found: {target_id}"}],
            }
            self._send_json_rpc(req_id, result=result)
            return

        if tool_name == "describe_schema":
            result = {
                "structuredContent": {
                    "type": "AirQualityObserved",
                    "properties": {"temperature": {"type": "number"}},
                }
            }
            self._send_json_rpc(req_id, result=result)
            return

        if tool_name == "delete_entity":
            result = {"structuredContent": {"deleted": True}}
            self._send_json_rpc(req_id, result=result)
            return

        self._send_json_rpc(req_id, error={"code": -32601, "message": f"Tool not found: {tool_name}"})


def serve(
    port: int = 0,
    mode: str = "conforming",
    agent_token: str = "agent-token",
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Starts the stub MCP server on 127.0.0.1 in an in-process daemon thread."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), StubMcpHandler)
    httpd.mode = mode
    httpd.agent_token = agent_token
    httpd.sessions = set()

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stub MCP Server for conformance testing")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--mode", choices=["conforming", "broken"], default="conforming", help="Server mode")
    parser.add_argument("--agent-token", default="agent-token", help="Bearer token for agent identity")
    args = parser.parse_args()

    server, _ = serve(port=args.port, mode=args.mode, agent_token=args.agent_token)
    print(f"Stub MCP server running on port {server.server_address[1]} in {args.mode} mode...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

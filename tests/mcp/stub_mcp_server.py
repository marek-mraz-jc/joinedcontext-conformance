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
| 12 | The Endpoint serves `/ws` (426) while `describe_access` names no `liveUpdates` | T-5045 live updates (`test_describe_access_names_the_websocket_an_agent_can_open`) |
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jsonschema
import stub_connector

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



#: The hub's one answer for a slug the caller cannot read (platform `mcp/hub.rs` OUTSIDE_REACH, T-6031).
HUB_OUTSIDE_REACH = (
    "no Endpoint you may read has that slug: list_endpoints lists the ones you may read, and an "
    "Endpoint that is not public is read after signing in with an account its Policy grants"
)

#: What a caller without a token may list and call (platform `mcp/tools.rs` ANONYMOUS_TOOLS, T-4168).
ANONYMOUS_TOOLS = frozenset(
    {
        "list_endpoints",
        "get_endpoint",
        "search",
        "search_documents",
        "read_knowledge_context",
        "list_types",
        "get_type",
        "list_type_details",
        "list_attributes",
        "list_attribute_details",
        "get_attribute",
        "query_entities",
        "summarize",
        "get_entity",
        "get_attribute_value",
        "query_temporal",
        "retrieve_temporal",
        "describe_schema",
        "describe_access",
    }
)

#: The Endpoints the hub door `/api/mcp` serves: a caller without a token reads the public one
#: alone, and a non-public slug answers exactly as an unknown one (T-6026). Defect (broken mode):
#: the hub names the non-public Endpoint to everyone.
HUB_ENDPOINTS = {
    "aq-public": ("Air quality", True),
    "rec-internal": ("Records of the region", False),
    "tr-public": ("Traffic counts", True),
}

#: The rows a `list_endpoints` call without `limit` answers here: one, so the two public
#: Endpoints take two pages and a client has to follow nextCursor (T-6061; the gateway's is 20).
HUB_LIST_PAGE = 1


#: The hub's search of its knowledge sources (EP-133, T-4659), answered with one fixed hit.
SEARCH_DOCUMENTS = {
    "name": "search_documents",
    "description": "Search the public pages and documents of banskabystrica.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            # T-6167: the question in each content language, merged by rank fusion.
            "translations": {"type": "object", "maxProperties": 3,
                             "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 300}},
            # T-6183: hits whose path starts with one of these; the next page of the same search.
            "pathPrefix": {"anyOf": [
                {"type": "string", "pattern": "^/", "maxLength": 200},
                {"type": "array", "minItems": 1, "maxItems": 5,
                 "items": {"type": "string", "pattern": "^/", "maxLength": 200}},
            ]},
            "cursor": {"type": "string", "maxLength": 100},
        },
        "required": ["query"],
    },
    "annotations": {"readOnlyHint": True},
}
#: The passages around one search_documents hit (EP-167, T-6183; gateway `mcp/documents.rs`).
READ_KNOWLEDGE_CONTEXT = {
    "name": "read_knowledge_context",
    "description": "Read the passages before and after a search_documents hit, in page order.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "chunkId": {"type": "integer", "minimum": 1},
            "before": {"type": "integer", "minimum": 0, "maximum": 10},
            "after": {"type": "integer", "minimum": 0, "maximum": 10},
        },
        "required": ["chunkId"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True},
}
#: The pages the hub's knowledge source holds: (path, passages in page order). A passage's
#: chunkId is 10 * page number + ordinal.
KNOWLEDGE_PAGES = [
    ("/sk/urad/klientske-centrum", ["Klientske centrum", "Pondelok 8:00 až 17:00", "Utorok 8:00 až 15:00"]),
    ("/sk/urad/podatelna", ["Podateľňa", "Streda 8:00 až 17:00", "Piatok 8:00 až 12:00", "Sobota zatvorené"]),
    ("/fi/palvelut/asiakaspalvelu", ["Asiakaspalvelu", "Aukioloajat ma-pe 8-16"]),
]
#: The ranked hits of every question, best first, as (page number, ordinal).
KNOWLEDGE_RANKING = [(1, 1), (2, 1), (3, 1), (1, 2), (2, 2), (1, 0), (2, 3), (3, 0)]
SEARCH_HIT = {
    "title": "Klientske centrum", "url": "https://www.banskabystrica.sk/kc", "publisher": "banskabystrica",
    "description": None, "published": None, "modified": "2026-09-30T08:12:00Z", "language": "sk",
    "text": "Pondelok 8:00 až 17:00", "score": 0.03,
    "cite": "Klientske centrum (banskabystrica), https://www.banskabystrica.sk/kc, modified 2026-09-30",
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

    def _sanitized(self, space: str) -> list[dict]:
        """The entities of one space as a caller may read them: the masked attribute gone."""
        served = []
        for entity in ENTITIES_BY_SPACE.get(space, []):
            copy_e = dict(entity)
            copy_e.pop("secretReading", None)
            served.append(copy_e)
        return served

    def _rest_entities(self, path: str) -> bool:
        """The NGSI-LD read surface of the endpoint, beside its MCP one (T-1860).

        The parity suite sends one request to each door and compares the answers, so the stub
        has to have both. In broken mode this door serves one entity fewer than the tool does,
        which is the defect the suite exists to catch.
        """
        marker = "/ngsi-ld/v1/entities"
        if marker not in path:
            return False
        space = "ovzdusie"
        segments = path.split("/")
        if "cs" in segments[:-1]:
            space = segments[segments.index("cs") + 1]
        served = self._sanitized(space)
        tail = path.split(marker, 1)[1].strip("/")
        if tail:
            wanted = next((e for e in served if e["id"] == tail), None)
            if wanted is None:
                body = json.dumps(
                    {
                        "type": "https://uri.etsi.org/ngsi-ld/errors/ResourceNotFound",
                        "title": "Resource not found",
                        "detail": f"Entity not found: {tail}",
                    }
                ).encode("utf-8")
                self._send_bytes(404, "application/problem+json", body)
                return True
            self._send_bytes(200, "application/json", json.dumps(wanted).encode("utf-8"))
            return True
        if self.server.mode == "broken":
            served = served[:-1]
        self._send_bytes(200, "application/json", json.dumps(served).encode("utf-8"))
        return True

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if self._rest_entities(path) or stub_connector.handle_get(self, path):
            return
        if path == "/.well-known/oauth-protected-resource/cs/tajne/mcp":
            host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
            metadata = {
                "resource": f"http://{host}/cs/tajne/mcp",
                "authorization_servers": [f"http://{host}/oauth/token"],
            }
            body = json.dumps(metadata).encode("utf-8")
            self._send_bytes(200, "application/json", body)
            return

        if path.endswith("/api/endpoint/aq-public/ws") and self.server.mode == "broken":
            # Defect 12 (T-5045): a socket the access document never names.
            self._send_bytes(426, "text/plain", b"upgrade to a WebSocket")
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

    # The rendered formalisms `describe_schema` serves, each small and each really of the
    # language it claims (T-1860): the parity suite parses them rather than reading them.
    FORMALISMS = {
        "linkml": {
            "format": "linkml",
            "mediaType": "text/yaml",
            "document": (
                "id: urn:joinedcontext:model:ovzdusie:v1\n"
                "name: ovzdusie\n"
                "classes:\n"
                "  AirQualityObserved:\n"
                "    attributes:\n"
                "      temperature:\n"
                "        range: float\n"
            ),
        },
        "shacl": {
            "format": "shacl",
            "mediaType": 'text/turtle; profile="shacl"',
            "document": (
                "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
                "@prefix m: <urn:joinedcontext:model:ovzdusie:v1:> .\n\n"
                "<urn:joinedcontext:model:ovzdusie:v1:AirQualityObservedShape>\n"
                "  a sh:NodeShape ;\n"
                "  sh:targetClass m:AirQualityObserved ;\n"
                "  sh:closed false .\n"
            ),
        },
        "rdf": {
            "format": "rdf",
            "mediaType": "text/turtle",
            "document": (
                "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
                "@prefix m: <urn:joinedcontext:model:ovzdusie:v1:> .\n\n"
                "m:AirQualityObserved a rdfs:Class .\n"
            ),
        },
    }

    def _is_hub(self, path: str) -> bool:
        return path.endswith("/cs/hub/mcp")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        # read the body before any early return: an unread body desynchronizes the keep-alive
        # connection and the next request on it comes back as a 400 from http.server itself
        raw_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if stub_connector.handle_post(self, path, raw_body):
            return

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
        # T-4168: a data-plane caller without a token lists and calls the read tools alone.
        # Defect (broken mode): the write tools stay open to everyone.
        anonymous = (
            not is_config_plane
            and self.server.mode != "broken"
            and "Bearer " not in self.headers.get("Authorization", "")
        )

        if method == "tools/list":
            tools = self._list_tools(is_config_plane)
            if anonymous:
                tools = [tool for tool in tools if tool["name"] in ANONYMOUS_TOOLS]
            if self._is_hub(path) and self.server.mode != "broken":
                # A hub over registrations is a read: the write tools of an ordinary space
                # have nothing to write to here (PF-48).
                tools = [
                    tool for tool in tools
                    if tool.get("annotations", {}).get("readOnlyHint") is True
                ]
            if self._is_hub(path) and self.server.mode != "broken":
                tools = tools + [SEARCH_DOCUMENTS, READ_KNOWLEDGE_CONTEXT]
            if path == "/api/mcp" and self.server.mode == "broken" and tools:
                # EP-112 / defect: the connector lists one tool under a name of its own.
                tools = [dict(tools[0], name=tools[0]["name"] + "_v0")] + tools[1:]
            self._send_json_rpc(req_id, result={"tools": tools})
            return

        if method == "tools/call":
            params = req.get("params") or {}
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            if anonymous and tool_name not in ANONYMOUS_TOOLS:
                self._send_json_rpc(
                    req_id,
                    error={"code": -32602, "message": f"`{tool_name}` is not offered here: this surface reads only"},
                )
                return
            if path == "/api/mcp" and tool_name in ("get_endpoint", "list_endpoints", "search", "query_entities"):
                self._hub_door(req_id, tool_name, tool_args, anonymous)
                return
            if tool_name == "search_documents" and self._is_hub(path) and self.server.mode != "broken":
                invalid = next(
                    (f"invalid parameter for tool search_documents: {e.message}"
                     for e in jsonschema.Draft7Validator(SEARCH_DOCUMENTS["inputSchema"]).iter_errors(tool_args)),
                    None,
                )
                page = None if invalid else self._search_page(tool_args)
                if invalid or isinstance(page, str):
                    self._send_json_rpc(req_id, error={"code": -32602, "message": invalid or page})
                else:
                    self._send_json_rpc(req_id, result={"content": [{"type": "text", "text": f"{len(page['hits'])} hits"}],
                                                        "structuredContent": page, "isError": False,
                                                        "_meta": {"jc/untrusted": True}})
                return
            if tool_name == "read_knowledge_context" and self._is_hub(path) and self.server.mode != "broken":
                invalid = next(
                    (f"invalid parameter for tool read_knowledge_context: {e.message}"
                     for e in jsonschema.Draft7Validator(READ_KNOWLEDGE_CONTEXT["inputSchema"]).iter_errors(tool_args)),
                    None,
                )
                passages = None if invalid else self._passages_around(tool_args)
                if invalid or passages is None:
                    self._send_json_rpc(req_id, error={"code": -32602, "message": invalid or "`chunkId` names no passage of this hub"})
                else:
                    self._send_json_rpc(req_id, result={"content": [{"type": "text", "text": f"{len(passages)} passages"}],
                                                        "structuredContent": {"passages": passages}, "isError": False,
                                                        "_meta": {"jc/untrusted": True}})
                return
            invalid = self._schema_violation(tool_name, tool_args, is_config_plane)
            if invalid and self.server.mode != "broken":
                # AG-21: parameters are validated against the published draft-07 schema before
                # anything downstream sees them. Defect 10 (broken mode) skips this on purpose.
                self._send_json_rpc(req_id, error={"code": -32602, "message": invalid})
                return
            slug = tool_args.get("endpoint")
            # Only a well-formed slug (EP-03: 26+ chars of base32) is answered as outside reach;
            # the gateway answers any other with a -32602 naming the closest of the caller's.
            well_formed = isinstance(slug, str) and re.fullmatch(r"[a-z2-7]{26,}", slug)
            if self._is_hub(path) and well_formed and slug not in self.HUB_MEMBERS:
                if self.server.mode == "broken":
                    # T-6031 / defect: a slug outside reach answers 401, which a client reads
                    # as the whole connector signed out.
                    self._send_bytes(401, "application/json", b'{"error":"unauthorized"}')
                    return
                # EP-87: one tool error for every slug outside reach, naming none (SP-20).
                self._send_json_rpc(req_id, result={"isError": True, "content": [{"type": "text", "text": HUB_OUTSIDE_REACH}]})
                return
            self._call_tool(req_id, tool_name, tool_args, is_config_plane, path)
            return

        self._send_json_rpc(req_id, error={"code": -32601, "message": f"Method not found: {method}"})

    @staticmethod
    def _passage(page: int, ordinal: int) -> dict:
        path, texts = KNOWLEDGE_PAGES[page - 1]
        return {"chunkId": 10 * page + ordinal, "ordinal": ordinal, "path": path, "text": texts[ordinal]}

    def _search_page(self, tool_args: dict) -> dict | str:
        """One search_documents page (T-6183): the ranked hits kept to `pathPrefix`, cut at the
        offset a cursor of the same arguments names. Answers the refusal text otherwise."""
        prefixes = tool_args.get("pathPrefix") or []
        prefixes = [prefixes] if isinstance(prefixes, str) else prefixes
        hits = [
            dict(SEARCH_HIT, **self._passage(page, ordinal))
            for page, ordinal in KNOWLEDGE_RANKING
            if not prefixes or any(KNOWLEDGE_PAGES[page - 1][0].startswith(p) for p in prefixes)
        ]
        # The gateway seals a cursor to the caller and every argument but limit and cursor.
        bound = json.dumps({k: v for k, v in tool_args.items() if k not in ("limit", "cursor")}, sort_keys=True)
        start = 0
        if "cursor" in tool_args:
            offset, _, seal = tool_args["cursor"].partition(".")
            if not offset.isdigit() or seal != str(uuid.uuid5(uuid.NAMESPACE_URL, bound)):
                return "`cursor` is not a nextCursor of this search: pass the one the last page answered, with the same arguments"
            start = int(offset)
        limit = int(tool_args.get("limit") or 5)
        page = {"hits": hits[start : start + limit]}
        if start + limit < len(hits):
            page["nextCursor"] = f"{start + limit}.{uuid.uuid5(uuid.NAMESPACE_URL, bound)}"
        return page

    def _passages_around(self, tool_args: dict) -> list[dict] | None:
        """The passages of the hit's page from `before` ahead of it to `after` past it (T-6183)."""
        page, ordinal = divmod(int(tool_args["chunkId"]), 10)
        if not 1 <= page <= len(KNOWLEDGE_PAGES) or ordinal >= len(KNOWLEDGE_PAGES[page - 1][1]):
            return None
        first = max(0, ordinal - int(tool_args.get("before", 2)))
        last = min(len(KNOWLEDGE_PAGES[page - 1][1]) - 1, ordinal + int(tool_args.get("after", 2)))
        return [dict(self._passage(page, o), hit=o == ordinal) for o in range(first, last + 1)]

    def _hub_door(self, req_id, tool_name: str, tool_args: dict, anonymous: bool) -> None:
        """The gateway hub's catalogue tools over HUB_ENDPOINTS (T-6026)."""
        # `anonymous` is never true in broken mode, so the defect reads every Endpoint.
        readable = {slug: title for slug, (title, open_) in HUB_ENDPOINTS.items() if open_ or not anonymous}
        slug = str(tool_args.get("endpoint", ""))
        if tool_name == "query_entities" and slug not in readable:
            # EP-87 (T-6031): the one tool error for every slug outside reach, a non-public one alike.
            self._send_json_rpc(req_id, result={"isError": True, "content": [{"type": "text", "text": HUB_OUTSIDE_REACH}]})
            return
        if tool_name == "get_endpoint" and slug not in readable:
            self._send_json_rpc(req_id, result={"isError": False, "content": [
                {"type": "text", "text": f"no Endpoint you may read is called `{slug}`; closest: aq-public (Air quality)"}]})
            return
        if tool_name == "get_endpoint":
            text = f"NGSI-LD Endpoint {slug} ({readable[slug]})"
        elif tool_name == "query_entities":
            text = json.dumps({"entities": self._sanitized("ovzdusie")})
        elif tool_name == "list_endpoints":
            self._send_json_rpc(req_id, result=self._endpoint_page(readable, tool_args))
            return
        else:
            words = str(tool_args.get("query", "")).lower().split()
            rows = [(s, t) for s, t in readable.items() if any(w in t.lower() for w in words)]
            text = "slug | title\n" + "\n".join(f"{s} | {t}" for s, t in rows)
        self._send_json_rpc(req_id, result={"isError": False, "content": [{"type": "text", "text": text}]})

    @staticmethod
    def _endpoint_page(readable: dict, tool_args: dict) -> dict:
        """One `list_endpoints` page (T-6061): `limit` rows from the offset `cursor` names."""
        rows = sorted(readable.items())
        start = int(tool_args.get("cursor") or 0)
        limit = int(tool_args.get("limit") or HUB_LIST_PAGE)
        page = rows[start : start + limit]
        structured = {"endpoints": [{"slug": s, "title": t} for s, t in page], "total": len(rows)}
        text = "slug | title\n" + "".join(f"{s} | {t}\n" for s, t in page) + f"total {len(rows)}"
        if start + limit < len(rows):
            structured["nextCursor"] = str(start + limit)
            text += f"; nextCursor {start + limit}"
        return {"isError": False, "content": [{"type": "text", "text": text}], "structuredContent": structured}

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
                    "name": "describe_access",
                    "description": "What the caller's grant allows on this Endpoint",
                    "inputSchema": {"type": "object", "properties": {"format": {"type": "string"}}},
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
            # The masked attribute is gone here as it is on the REST door, so the two answer
            # one entity (T-1860); in broken mode the raw entity goes out, which is the leak
            # the isolation suite is looking for.
            current_space_entities = (
                ENTITIES_BY_SPACE.get(space, [])
                if self.server.mode == "broken"
                else self._sanitized(space)
            )
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
            rendered = self.FORMALISMS.get(tool_args.get("format", ""))
            if rendered is not None:
                # Under the tool's own key, as the gateway answers (API/02, `result_key`).
                self._send_json_rpc(req_id, result={"structuredContent": {"schema": rendered}})
                return
            result = {
                "structuredContent": {
                    "type": "AirQualityObserved",
                    "recommended": "linkml",
                    "properties": {"temperature": {"type": "number"}},
                }
            }
            self._send_json_rpc(req_id, result=result)
            return

        if tool_name == "describe_access":
            # Defect (broken mode): the anonymous grant holds a write.
            actions = ["queryEntity", "retrieveEntity"] + (["createEntity"] if self.server.mode == "broken" else [])
            permissions = [{"resource": {"type": "AirQualityObserved"}, "actions": actions, "attributes": "*"}]
            # Shaped as the gateway's jsonrpc::answered: the text and structuredContent.access hold
            # one document. The stub serves no WebSocket, so it names no liveUpdates (T-5045).
            access = {"permissions": permissions}
            result = {"content": [{"type": "text", "text": json.dumps(access)}], "structuredContent": {"access": access}}
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

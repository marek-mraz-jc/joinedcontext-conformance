"""Lightweight HTTP stub server for AuthZEN and ODRL access testing with planted defect modes."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

CONFORMING_JSON_DOC: dict[str, Any] = {
    "subject": {"type": "user", "id": "did:web:banskabystrica.sk:users:viewer"},
    "resource": {"type": "endpoint", "id": "zt4qm7ge2xdv6ksb3ncf5arw2y", "space": "ovzdusie"},
    "permissions": [
        {
            "resource": {
                "type": "AirQualityObserved",
                "idPatterns": ["^urn:ngsi-ld:AirQualityObserved:banskabystrica\\.sk:ovzdusie:.*$"],
            },
            "actions": ["retrieveEntity", "queryEntity", "queryTemporal"],
            "attributes": ["dateObserved", "location", "pm10", "pm25"],
            "constraints": {
                "geoQ": "georel=within;geometry=Polygon;coordinates=[[[19.1,48.7],[19.2,48.7],[19.2,48.8],[19.1,48.8],[19.1,48.7]]]",
                "scopeQ": "/geo/SK/BB",
                "temporalQ": "timerel=after;timeAt=P-1D",
                "q": "pm10>0",
            },
        },
        {
            "resource": {
                "type": "AirQualityObserved",
                "idPatterns": ["^urn:ngsi-ld:AirQualityObserved:banskabystrica\\.sk:ovzdusie:.*$"],
            },
            "actions": ["updateEntity"],
            "attributes": ["pm10", "pm25"],
            "constraints": {
                "geoQ": "georel=within;geometry=Polygon;coordinates=[[[19.1,48.7],[19.2,48.7],[19.2,48.8],[19.1,48.8],[19.1,48.7]]]",
                "scopeQ": "/geo/SK/BB",
                "temporalQ": "timerel=after;timeAt=P-1D",
                "q": "pm10>0",
            },
        },
        {
            "resource": {"type": "District"},
            "actions": ["retrieveEntity", "queryEntity"],
            "attributes": ["location", "name"],
            "constraints": {},
        },
    ],
    "prohibitions": [],
    "limits": {
        "requestsPerMinute": 600,
    },
}

CONFORMING_ODRL_DOC: dict[str, Any] = {
    "@context": [
        "http://www.w3.org/ns/odrl.jsonld",
        "https://joinedcontext.com/odrl/ngsi-ld/v1/context.jsonld",
    ],
    "@type": "Policy",
    "uid": "urn:joinedcontext:grant:zt4qm7ge2xdv6ksb3ncf5arw2y",
    "permission": [
        {
            "action": ["retrieveEntity", "queryEntity", "queryTemporal"],
            "target": {
                "@type": "ngsi-ld:EntityType",
                "uid": "AirQualityObserved",
                "refinement": [
                    {
                        "leftOperand": "attrs",
                        "operator": "isAnyOf",
                        "rightOperand": ["dateObserved", "location", "pm10", "pm25"],
                    }
                ],
            },
            "assigner": "did:web:banskabystrica.sk",
            "assignee": "did:web:banskabystrica.sk:users:viewer",
            "constraint": [
                {
                    "leftOperand": "geoQ",
                    "operator": "eq",
                    "rightOperand": "georel=within;geometry=Polygon;coordinates=[[[19.1,48.7],[19.2,48.7],[19.2,48.8],[19.1,48.8],[19.1,48.7]]]",
                },
                {
                    "leftOperand": "scopeQ",
                    "operator": "eq",
                    "rightOperand": "/geo/SK/BB",
                },
                {
                    "leftOperand": "temporalQ",
                    "operator": "eq",
                    "rightOperand": "timerel=after;timeAt=P-1D",
                },
                {
                    "leftOperand": "q",
                    "operator": "eq",
                    "rightOperand": "pm10>0",
                },
            ],
        },
        {
            "action": ["updateEntity"],
            "target": {
                "@type": "ngsi-ld:EntityType",
                "uid": "AirQualityObserved",
                "refinement": [
                    {
                        "leftOperand": "attrs",
                        "operator": "isAnyOf",
                        "rightOperand": ["pm10", "pm25"],
                    }
                ],
            },
            "assigner": "did:web:banskabystrica.sk",
            "assignee": "did:web:banskabystrica.sk:users:viewer",
            "constraint": [
                {
                    "leftOperand": "geoQ",
                    "operator": "eq",
                    "rightOperand": "georel=within;geometry=Polygon;coordinates=[[[19.1,48.7],[19.2,48.7],[19.2,48.8],[19.1,48.8],[19.1,48.7]]]",
                },
                {
                    "leftOperand": "scopeQ",
                    "operator": "eq",
                    "rightOperand": "/geo/SK/BB",
                },
                {
                    "leftOperand": "temporalQ",
                    "operator": "eq",
                    "rightOperand": "timerel=after;timeAt=P-1D",
                },
                {
                    "leftOperand": "q",
                    "operator": "eq",
                    "rightOperand": "pm10>0",
                },
            ],
        },
        {
            "action": ["retrieveEntity", "queryEntity"],
            "target": {
                "@type": "ngsi-ld:EntityType",
                "uid": "District",
                "refinement": [
                    {
                        "leftOperand": "attrs",
                        "operator": "isAnyOf",
                        "rightOperand": ["location", "name"],
                    }
                ],
            },
            "assigner": "did:web:banskabystrica.sk",
            "assignee": "did:web:banskabystrica.sk:users:viewer",
            "constraint": [],
        },
    ],
}

TURTLE_BODY = """@prefix odrl: <http://www.w3.org/ns/odrl/2/> .
@prefix ngsi-ld: <https://joinedcontext.com/odrl/ngsi-ld/v1#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<urn:joinedcontext:grant:zt4qm7ge2xdv6ksb3ncf5arw2y> a odrl:Policy ;
    odrl:uid "urn:joinedcontext:grant:zt4qm7ge2xdv6ksb3ncf5arw2y" ;
    odrl:profile <https://joinedcontext.com/odrl/ngsi-ld/v1/context.jsonld> ;
    odrl:permission [
        odrl:action "retrieveEntity", "queryEntity", "queryTemporal" ;
        odrl:target <AirQualityObserved> ;
        odrl:assigner <did:web:banskabystrica.sk> ;
        odrl:assignee <did:web:banskabystrica.sk:users:viewer> ;
        odrl:constraint [
            odrl:leftOperand ngsi-ld:geoQ ;
            odrl:operator odrl:eq ;
            odrl:rightOperand "georel=within;geometry=Polygon;coordinates=[[[19.1,48.7],[19.2,48.7],[19.2,48.8],[19.1,48.8],[19.1,48.7]]]"
        ]
    ] .
"""


class StubAccessHandler(BaseHTTPRequestHandler):
    mode: str = "conforming"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if path.endswith("/access"):
            accept = self.headers.get("Accept", "application/json")
            if "application/odrl+json" in accept:
                self._handle_odrl()
            elif "text/turtle" in accept:
                self._handle_turtle()
            else:
                self._handle_json_access()
            return

        if "/entities" in path:
            self._handle_entities(query)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.endswith("/access/check"):
            self._handle_check()
            return

        self.send_response(404)
        self.end_headers()

    def do_DELETE(self) -> None:
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"type": "OperationNotAllowed", "title": "Forbidden"}).encode("utf-8"))

    def _handle_json_access(self) -> None:
        doc = copy.deepcopy(CONFORMING_JSON_DOC)
        if self.mode == "disclosing":
            # A type the caller may not touch, listed anyway, and an attribute nobody may
            # read named beside the ones they may (EP-59, R20).
            doc["permissions"].append(
                {
                    "resource": {"type": "ServiceAccount"},
                    "actions": ["retrieveEntity"],
                    "attributes": ["name"],
                    "constraints": {},
                }
            )
            doc["permissions"][2]["attributes"].append("stationApiKey")
        elif self.mode == "contradictory":
            # A grant over no attribute at all: the shape says "you may query this type and
            # read nothing of it", which no policy means. `"*"` is how a whole type is said.
            doc["permissions"][0]["attributes"] = []

        body = json.dumps(doc).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_odrl(self) -> None:
        doc = copy.deepcopy(CONFORMING_ODRL_DOC)
        if self.mode == "lossy_odrl":
            doc["permission"] = [
                p for p in doc["permission"] if p.get("action") != ["updateEntity"]
            ]
            for p in doc["permission"]:
                p["constraint"] = [
                    c for c in p.get("constraint", []) if c.get("leftOperand") != "scopeQ"
                ]

        body = json.dumps(doc).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/odrl+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_turtle(self) -> None:
        body = TURTLE_BODY.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/turtle")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_check(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        req_bytes = self.rfile.read(length) if length > 0 else b"{}"
        try:
            req_data = json.loads(req_bytes)
        except Exception:
            req_data = {}

        res_type = req_data.get("resource", {}).get("type", "")
        action_name = req_data.get("action", {}).get("name", "")

        is_allowed = (
            res_type == "AirQualityObserved"
            and action_name in ("retrieveEntity", "queryEntity", "queryTemporal", "updateEntity")
        )

        if is_allowed:
            resp_doc: dict[str, Any] = {"decision": True}
        else:
            if self.mode == "check_leaks":
                resp_doc = {
                    "decision": False,
                    "context": {
                        "reason_admin": "Denied by urn:ngsi-ld:Policy:banskabystrica.sk:ovzdusie:public-air-quality rule 1"
                    },
                }
            else:
                resp_doc = {
                    "decision": False,
                    "context": {"reason_user": "Action not permitted on resource"},
                }

        body = json.dumps(resp_doc).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_entities(self, query: dict[str, list[str]]) -> None:
        t = query.get("type", [""])[0]
        if t == "AirQualityObserved":
            entities = [
                {
                    "id": "urn:ngsi-ld:AirQualityObserved:BB:001",
                    "type": "AirQualityObserved",
                    "dateObserved": "2026-08-14T10:00:00Z",
                    "location": {"type": "Point", "coordinates": [19.15, 48.73]},
                    "pm10": 18.5,
                    "pm25": 9.2,
                }
            ]
        else:
            entities = []

        body = json.dumps(entities).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubAccessServer:
    def __init__(self, httpd: HTTPServer, thread: threading.Thread) -> None:
        self.httpd = httpd
        self.thread = thread
        self.port: int = httpd.server_address[1]
        self.url: str = f"http://127.0.0.1:{self.port}"

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def serve(port: int = 0, mode: str = "conforming") -> StubAccessServer:
    handler_cls = type(
        f"StubAccessHandler_{mode}",
        (StubAccessHandler,),
        {"mode": mode},
    )
    httpd = HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return StubAccessServer(httpd, t)

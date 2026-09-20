from __future__ import annotations

import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
from typing import Any

FIXTURE_ENTITIES: list[dict[str, Any]] = [
    {
        "id": "urn:ngsi-ld:AirQualityObserved:station-001",
        "type": "AirQualityObserved",
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [17.1077, 48.1486]},
        },
        "pm25": {"type": "Property", "value": 14.5},
        "temperature": {"type": "Property", "value": 21.0},
        "refStation": {
            "type": "Relationship",
            "object": "urn:ngsi-ld:Station:central-01",
        },
        "notes": {"type": "Property", "value": None},
        "internalAuditSecret": {"type": "Property", "value": "classified-sensor-key-99"},
    },
    {
        "id": "urn:ngsi-ld:AirQualityObserved:station-002",
        "type": "AirQualityObserved",
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [17.1122, 48.1523]},
        },
        "pm25": {"type": "Property", "value": 18.2},
        "temperature": {"type": "Property", "value": 19.5},
        "internalAuditSecret": {"type": "Property", "value": "classified-sensor-key-100"},
    },
    {
        "id": "urn:ngsi-ld:AirQualityObserved:station-003",
        "type": "AirQualityObserved",
        "pm25": {"type": "Property", "value": 12.0},
        "internalAuditSecret": {"type": "Property", "value": "classified-sensor-key-101"},
    },
]


def project_entity(entity: dict[str, Any], drop_hidden: bool = True) -> dict[str, Any]:
    copy_ent = json.loads(json.dumps(entity))
    if drop_hidden:
        copy_ent.pop("internalAuditSecret", None)
    return copy_ent


class RepresentationStubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        mode = getattr(self.server, "mode", "conforming")
        stub_only = getattr(self.server, "stub_only", None)

        if stub_only:
            if stub_only == "ngsi-ld" and not path.endswith("ngsi-ld/v1/entities"):
                return self._send(404, "application/json", b'{"detail": "disabled"}')

        # 1. NGSI-LD
        if path.endswith("ngsi-ld/v1/entities"):
            entities = [project_entity(e, drop_hidden=True) for e in FIXTURE_ENTITIES]
            return self._send(200, "application/ld+json", json.dumps(entities).encode("utf-8"))

        # 2. CSV
        if path.endswith("file.csv"):
            drop = False if mode == "leaky" else True
            entities = [project_entity(e, drop_hidden=drop) for e in FIXTURE_ENTITIES]
            out = io.StringIO()
            fieldnames = ["id", "pm25", "temperature", "refStation", "notes"]
            if not drop:
                fieldnames.append("internalAuditSecret")
            writer = csv.DictWriter(out, fieldnames=fieldnames)
            writer.writeheader()
            for e in entities:
                row: dict[str, Any] = {"id": e["id"]}
                for k in fieldnames[1:]:
                    if k in e and isinstance(e[k], dict):
                        val = e[k].get("value") if e[k].get("type") != "Relationship" else e[k].get("object")
                        row[k] = "" if val is None else str(val)
                    else:
                        row[k] = ""
                writer.writerow(row)
            return self._send(200, "text/csv; charset=utf-8", out.getvalue().encode("utf-8"))

        # 3. GeoJSON
        if path.endswith("file.geojson"):
            entities = [project_entity(e, drop_hidden=True) for e in FIXTURE_ENTITIES]
            if mode == "divergent":
                # Drop station-002 and round station-001 pm25 to 10
                entities = [e for e in entities if e["id"] != "urn:ngsi-ld:AirQualityObserved:station-002"]
                for e in entities:
                    if e["id"] == "urn:ngsi-ld:AirQualityObserved:station-001":
                        e["pm25"]["value"] = 10.0

            features = []
            for e in entities:
                geom = e.get("location", {}).get("value")
                props: dict[str, Any] = {"id": e["id"]}
                for k, v in e.items():
                    if k in ("id", "type", "location"):
                        continue
                    if isinstance(v, dict):
                        props[k] = v.get("value") if v.get("type") != "Relationship" else v.get("object")
                features.append({
                    "type": "Feature",
                    "id": e["id"],
                    "geometry": geom,
                    "properties": props,
                })
            fc = {"type": "FeatureCollection", "features": features}
            return self._send(200, "application/geo+json", json.dumps(fc).encode("utf-8"))

        # 4. OGC Features
        if "ogc/features/collections/" in path and path.endswith("/items"):
            entities = [project_entity(e, drop_hidden=True) for e in FIXTURE_ENTITIES]
            features = []
            for e in entities:
                geom = e.get("location", {}).get("value")
                props = {"id": e["id"]}
                for k, v in e.items():
                    if k in ("id", "type", "location"):
                        continue
                    if isinstance(v, dict):
                        props[k] = v.get("value") if v.get("type") != "Relationship" else v.get("object")
                features.append({
                    "type": "Feature",
                    "id": e["id"],
                    "geometry": geom,
                    "properties": props,
                })
            fc = {"type": "FeatureCollection", "features": features}
            return self._send(200, "application/geo+json", json.dumps(fc).encode("utf-8"))

        # 5. STA Observations
        if path.endswith("sta/v1.1/Observations"):
            observations = []
            for e in FIXTURE_ENTITIES:
                observations.append({
                    "@iot.id": e["id"],
                    "result": e.get("pm25", {}).get("value"),
                    "phenomenonTime": "2025-01-01T00:00:00Z",
                    "Datastream@iot.navigationLink": f"/sta/v1.1/Datastreams('{e['id']}')",
                })
            payload = {"value": observations}
            return self._send(200, "application/json", json.dumps(payload).encode("utf-8"))

        return self._send(404, "application/json", b'{"detail": "not found"}')


def serve(port: int = 0, mode: str = "conforming", stub_only: str | None = None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), RepresentationStubHandler)
    setattr(server, "mode", mode)
    setattr(server, "stub_only", stub_only)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server

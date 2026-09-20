#!/usr/bin/env python3
"""Proof that the platform journey suite goes red at each stage that can break (T-0331).

Almost every claim in `test_platform_full_journey.py` needs a running platform, so this stands
one up in this process — a space surface, an Endpoint with two representations and a schema, a
second space federating the first, and a CKAN — and then serves it broken, one stage at a time:

     1. a coherent journey                          -> every test passes
     2. twenty-nine buses instead of thirty         -> the full-set test fails
     3. an entity minted under a foreign prefix     -> the tenancy test fails
     4. an entity with no location                  -> the map test fails
     5. a GeoJSON missing one feature               -> the two-representations test fails
     6. a hidden attribute in the entities          -> the EP-61 representation test fails
     7. a hidden attribute named in the schema      -> the EP-61 schema test fails
     8. the Endpoint serving what the space lacks   -> the narrowing test fails
     9. a catalogue resource that answers 404       -> the resource test fails
    10. the federated space answering fewer         -> the federation test fails
    11. an entity carrying no ingest relationship   -> the graph-link test fails

    python3 tests/e2e/selftest.py
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
HIDDEN = "operatorNote"
EXPECTED = 30
DATASET = "hsl-transport"


def bus(number: int) -> dict:
    return {
        "id": f"urn:ngsi-ld:Vehicle:hel.fi:transport:bus-{1000 + number}",
        "type": "Vehicle",
        "location": {
            "type": "GeoProperty",
            "value": {"type": "Point", "coordinates": [24.9 + number / 1000, 60.1 + number / 1000]},
        },
        "speed": {"type": "Property", "value": float(number)},
        "refDataSource": {
            "type": "Relationship",
            "object": "urn:ngsi-ld:DataSource:hel.fi:transport:hfp-ingest",
        },
    }


FLEET = [bus(n) for n in range(EXPECTED)]


def space_entities(case: str) -> list[dict]:
    fleet = copy.deepcopy(FLEET)
    if case == "short-run":
        return fleet[:-1]
    if case == "foreign-prefix":
        fleet[0]["id"] = "urn:ngsi-ld:Vehicle:elsewhere.example:transport:bus-1000"
    if case == "no-location":
        fleet[1].pop("location")
    if case == "leaked-entity":
        fleet[2][HIDDEN] = {"type": "Property", "value": "driver on break"}
    if case == "unlinked":
        fleet[3].pop("refDataSource")
    return fleet


def endpoint_entities(case: str) -> list[dict]:
    served = space_entities(case)
    if case == "endpoint-widens":
        served = served + [bus(900)]
    return served


def geojson(case: str) -> dict:
    served = endpoint_entities(case)
    if case == "geojson-short":
        served = served[:-1]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": entity["id"],
                "geometry": (entity.get("location") or {}).get("value"),
                "properties": {
                    key: value
                    for key, value in entity.items()
                    if key not in {"id", "type", "location"}
                },
            }
            for entity in served
        ],
    }


def schema(case: str) -> dict:
    properties = {"id": {"type": "string"}, "location": {"type": "object"}}
    if case == "leaked-schema":
        properties[HIDDEN] = {"type": "string"}
    return {"title": "Vehicle", "type": "object", "properties": properties}


def package(case: str, base: str) -> dict:
    url = f"{base}/gone" if case == "dead-resource" else f"{base}/api/endpoint/x/file.geojson"
    return {
        "name": DATASET,
        "resources": [
            {"id": "res-geojson", "name": "GeoJSON", "url": url},
            {"id": "res-ngsild", "name": "NGSI-LD", "url": f"{base}/api/endpoint/x/ngsi-ld/v1/entities"},
        ],
    }


def federated(case: str) -> list[dict]:
    entities = space_entities(case)
    return entities[:-2] if case == "federation-short" else entities


CASES: dict[str, set[str]] = {
    "good": set(),
    "short-run": {"test_pl01_the_pipeline_left_a_full_set_of_vehicles"},
    "foreign-prefix": {"test_sp09_no_entity_in_the_space_was_minted_under_a_foreign_prefix"},
    "no-location": {"test_every_served_entity_can_be_drawn_on_a_map"},
    "geojson-short": {
        "test_ep01_the_two_representations_of_the_endpoint_describe_the_same_entities"
    },
    "leaked-entity": {"test_ep61_no_hidden_attribute_is_in_any_representation"},
    "leaked-schema": {"test_ep61_no_hidden_attribute_is_named_in_the_schema"},
    "endpoint-widens": {"test_the_endpoint_serves_what_the_space_holds"},
    "dead-resource": {"test_every_resource_of_the_published_dataset_resolves"},
    "federation-short": {
        "test_sp09_the_federated_space_answers_with_the_entities_of_the_registered_one"
    },
    "unlinked": {"test_every_served_entity_points_back_at_what_ingested_it"},
}


def handler(case: str, base: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # noqa: D102 - a quiet self-test
            pass

        def _send(self, status: int, body: object) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - the stdlib name
            path = urlparse(self.path).path
            if path == "/cs/transport/ngsi-ld/v1/entities":
                self._send(200, space_entities(case))
            elif path == "/cs/air-quality/ngsi-ld/v1/entities":
                self._send(200, federated(case))
            elif path == "/api/endpoint/x/ngsi-ld/v1/entities":
                self._send(200, endpoint_entities(case))
            elif path == "/api/endpoint/x/file.geojson":
                self._send(200, geojson(case))
            elif path == "/api/endpoint/x/schema/v1/json-schema":
                self._send(200, schema(case))
            elif path == "/api/3/action/package_show":
                self._send(200, {"success": True, "result": package(case, base)})
            elif path == "/gone":
                self._send(404, {"error": "no such resource"})
            else:
                self._send(404, {"error": path})

    return Handler


def run(case: str) -> set[str]:
    """The tests that failed for one case."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(case, ""))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    server.RequestHandlerClass = handler(case, base)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        environment = dict(
            os.environ,
            SPACE_URL=f"{base}/cs/transport/ngsi-ld/v1",
            FEDERATED_SPACE_URL=f"{base}/cs/air-quality/ngsi-ld/v1",
            ENDPOINT_URL=f"{base}/api/endpoint/x",
            CKAN_URL=base,
            CKAN_DATASET=DATASET,
            HIDDEN_ATTRIBUTES=HIDDEN,
            EXPECTED_VEHICLES=str(EXPECTED),
        )
        for name in ("SPACE_TOKEN", "GATEWAY_TOKEN", "APP_MANIFEST", "CKAN_API_TOKEN"):
            environment.pop(name, None)
        finished = subprocess.run(
            [sys.executable, "-m", "pytest", str(HERE), "-q", "-p", "no:cacheprovider",
             "--no-header", "-rfE"],
            capture_output=True,
            text=True,
            env=environment,
            cwd=HERE,
        )
    finally:
        server.shutdown()
        server.server_close()
    # A fixture that fails is reported as ERROR rather than FAILED, and a broken journey is
    # exactly the case where the fixture is the thing that cannot be built.
    return {
        line.split("::")[-1].split()[0]
        for line in finished.stdout.splitlines()
        if line.startswith(("FAILED", "ERROR"))
    }


def main() -> int:
    problems: list[str] = []
    for case, expected in CASES.items():
        failed = run(case)
        if failed != expected:
            problems.append(
                f"{case}: expected {sorted(expected) or 'no failure'}, got {sorted(failed) or 'none'}"
            )
            print(f"  {case}: MISMATCH")
        else:
            print(f"  {case}: ok ({len(expected)} expected failure(s))")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"{len(CASES)} cases: the suite goes red exactly where it should")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

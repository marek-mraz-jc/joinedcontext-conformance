#!/usr/bin/env python3
"""Proof that the endpoint schema suite goes red on an incoherent endpoint (T-0337).

The suite in `test_endpoint_schema.py` only runs against a live endpoint, so this stands one
up in this process and serves it deliberately broken, one break at a time:

1. a coherent endpoint                          -> every test passes
2. a digest the artifact does not hash to       -> the digest test fails
3. a media type the artifact is not served as   -> the same test fails
4. shapes that reject the entity it serves      -> the acceptance test fails
5. shapes with no constraint left               -> the rejection test fails
6. a record naming no access rights             -> the access-rights test fails
7. a record listing no schema artifact          -> the listing test fails

    python3 tests/models/selftest_endpoint.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures/bb-ovzdusie/spaces/ovzdusie/datamodels"

SHAPES = (FIXTURES / "shacl/bb-air-quality.v2.ttl").read_text(encoding="utf-8")
CONTEXT = (FIXTURES / "context/bb-air-quality.v2.jsonld").read_text(encoding="utf-8")
ENTITY = json.loads((FIXTURES / "examples/bb-air-quality.example.jsonld").read_text(encoding="utf-8"))

# Every constraint dropped: shapes that target the class and demand nothing of it.
TOOTHLESS = """@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix bb: <https://bb.example.sk/ns/air-quality#> .

bb:AirQualityObservedShape a sh:NodeShape ; sh:targetClass bb:AirQualityObserved .
"""

# One constraint the served entity cannot meet: pm10 must be a string here, and it is 18.4.
HOSTILE = SHAPES.replace("sh:path bb:pm10 ;", "sh:path bb:pm10 ;\n        sh:minCount 99 ;", 1)

CASES = {
    "good": set(),
    "bad-digest": {"test_ep68_each_artifact_answers_with_the_declared_type_and_digest"},
    "bad-media-type": {"test_ep68_each_artifact_answers_with_the_declared_type_and_digest"},
    "hostile-shapes": {"test_dm46_pyshacl_accepts_an_entity_the_endpoint_serves"},
    "toothless-shapes": {"test_dm46_the_served_shapes_reject_an_entity_that_breaks_the_model"},
    "no-access-rights": {"test_ep69_the_record_declares_its_access_rights"},
    "no-artifacts": {"test_ep68_every_schema_artifact_is_listed_with_its_formalism"},
}


def shapes_for(case: str) -> str:
    if case == "hostile-shapes":
        return HOSTILE
    if case == "toothless-shapes":
        return TOOTHLESS
    return SHAPES


def record_for(case: str, base: str) -> dict:
    """The DCAT-AP record an endpoint of this shape would answer with (EP-27, EP-68)."""
    artifacts = [
        ("model.shacl.ttl", shapes_for(case), "text/turtle", "https://www.w3.org/TR/shacl/"),
        ("context.jsonld", CONTEXT, "application/ld+json", "https://www.w3.org/TR/json-ld11/"),
    ]
    distributions = [
        {
            "@id": f"{base}/ngsi-ld/v1/",
            "@type": "dcat:Distribution",
            "dct:title": "NGSI-LD API",
            "dcat:accessURL": f"{base}/ngsi-ld/v1/",
            "dcat:mediaType": "application/ld+json",
        }
    ]
    for name, body, media_type, formalism in artifacts:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if case == "bad-digest":
            digest = "0" * 64
        if case == "bad-media-type":
            media_type = "application/json"
        distributions.append(
            {
                "@id": f"{base}/schema/v2/{name}",
                "@type": "dcat:Distribution",
                "dct:title": name,
                "dcat:accessURL": f"{base}/schema/v2/{name}",
                "dcat:mediaType": media_type,
                "dct:conformsTo": formalism,
                "spdx:checksum": {
                    "@type": "spdx:Checksum",
                    "spdx:checksumValue": digest,
                },
            }
        )
    if case == "no-artifacts":
        distributions = distributions[:1]

    record = {
        "@context": "https://www.w3.org/ns/dcat.jsonld",
        "@id": base,
        "@type": "dcat:Dataset",
        "dct:identifier": "k4y7pq2mzt6vhx3nbwrs5cjd8f",
        "dct:accessRights": "http://publications.europa.eu/resource/authority/access-right/PUBLIC",
        "dcat:distribution": distributions,
    }
    if case == "no-access-rights":
        del record["dct:accessRights"]
    return record


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    case = "good"
    base = ""

    def log_message(self, *args):
        pass

    def send(self, body: bytes, media_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            self.send(json.dumps(record_for(Stub.case, Stub.base)).encode(), "application/ld+json")
        elif path.endswith("model.shacl.ttl"):
            self.send(shapes_for(Stub.case).encode("utf-8"), "text/turtle")
        elif path.endswith("context.jsonld"):
            self.send(CONTEXT.encode("utf-8"), "application/ld+json")
        elif path.endswith("/ngsi-ld/v1/types"):
            body = {"type": "EntityTypeList", "typeList": ["AirQualityObserved"]}
            self.send(json.dumps(body).encode(), "application/ld+json")
        elif path.endswith("/ngsi-ld/v1/entities"):
            self.send(json.dumps([ENTITY]).encode(), "application/ld+json")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


def run_suite(base: str) -> tuple[int, str]:
    env = os.environ.copy()
    env["ENDPOINT_URL"] = base
    env.pop("PROJECTS_DIR", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(HERE / "test_endpoint_schema.py"),
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    Stub.base = base

    failures: list[str] = []
    for case, expected_red in CASES.items():
        Stub.case = case
        code, out = run_suite(base)
        if not expected_red:
            if code != 0:
                failures.append(f"a coherent endpoint failed the suite:\n{out}")
            elif "skipped" in out and "passed" not in out:
                failures.append(f"the suite skipped everything against a live stub:\n{out}")
            continue
        if code == 0:
            failures.append(f"case {case} passed the suite, and it should not:\n{out}")
        for name in expected_red:
            if name not in out:
                failures.append(f"case {case} did not fail {name}:\n{out}")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "ok: the endpoint suite passes a coherent endpoint and goes red on a wrong digest, "
        "a wrong media type, shapes that reject their own data, shapes that reject nothing, "
        "a record with no access rights and a record with no schema artifact"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

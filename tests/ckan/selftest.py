#!/usr/bin/env python3
"""Proof that the CKAN and DCAT-AP suite goes red on a broken publication (T-0320).

Most of `test_ckan_dcat.py` only runs against a live endpoint and a live CKAN, so this
stands both up in this process and serves them deliberately broken, one break at a time:

1.  a coherent publication                          -> every test passes
2.  a record with no access rights                  -> the mandatory-term and shape tests fail
3.  a distribution with no accessURL                -> the same two fail
4.  a record that is not a dcat:Dataset             -> the shapes report validating nothing
5.  no dataset in CKAN at all                       -> every CKAN test fails
6.  a resource URL that answers 404                 -> the resource test fails
7.  a DataStore table with no rows                  -> the row test fails
8.  the same entity twice in the table              -> the row test fails
9.  a masked attribute in the record                -> the record leak test fails
10. a masked attribute in a DataStore row           -> the DataStore leak test fails

    python3 tests/ckan/selftest.py
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
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
RECORD = json.loads((HERE / "fixtures/endpoint-record.jsonld").read_text(encoding="utf-8"))
MASKED = "operatorNote"

ROWS = [
    {"_id": 1, "entity_id": "urn:ngsi-ld:AirQualityObserved:bb:ovzdusie:sever-01", "temperature.value": 12.5},
    {"_id": 2, "entity_id": "urn:ngsi-ld:AirQualityObserved:bb:ovzdusie:juh-02", "temperature.value": -3.5},
]

CASES: dict[str, set[str]] = {
    "good": set(),
    "no-access-rights": {
        "test_ep27_the_served_record_carries_every_mandatory_term",
        "test_ep27_the_served_record_conforms_to_the_core_shapes",
    },
    "no-access-url": {
        "test_ep27_the_served_record_carries_every_mandatory_term",
        "test_ep27_the_served_record_conforms_to_the_core_shapes",
    },
    "not-a-dataset": {
        "test_ep27_the_served_record_carries_every_mandatory_term",
        "test_ep27_the_served_record_conforms_to_the_core_shapes",
    },
    "no-dataset": {
        "test_ep62_the_endpoint_became_exactly_one_dataset",
        "test_ep63_the_dataset_metadata_is_the_records_own",
        "test_ep64_every_resource_url_resolves",
        "test_ep65_the_datastore_table_holds_rows",
        "test_ep61_ep66_no_masked_attribute_reaches_the_datastore",
    },
    "dead-resource": {"test_ep64_every_resource_url_resolves"},
    "empty-datastore": {"test_ep65_the_datastore_table_holds_rows"},
    "duplicate-rows": {"test_ep65_the_datastore_table_holds_rows"},
    "leaked-record": {"test_ep61_ep66_no_masked_attribute_reaches_the_record"},
    "leaked-datastore": {"test_ep61_ep66_no_masked_attribute_reaches_the_datastore"},
}


def record_of(case: str, base: str) -> dict:
    record = copy.deepcopy(RECORD)
    for distribution in record["dcat:distribution"]:
        distribution["dcat:accessURL"] = f"{base}/endpoint/{Path(urlparse(distribution['dcat:accessURL']).path).name}"
    if case == "no-access-rights":
        record.pop("dct:accessRights")
    if case == "no-access-url":
        record["dcat:distribution"][1].pop("dcat:accessURL")
    if case == "not-a-dataset":
        record["@type"] = "dcat:Catalog"
    if case == "leaked-record":
        record["dcat:distribution"][0]["dct:conformsTo"] = MASKED
    return record


def package_of(case: str, base: str) -> dict:
    resources = [
        {"id": "res-csv", "name": "CSV", "url": f"{base}/endpoint/file.csv"},
        {"id": "res-rows", "name": "Rows", "url": f"{base}/endpoint/rows", "datastore_active": True},
    ]
    if case == "dead-resource":
        resources[0]["url"] = f"{base}/endpoint/gone"
    return {
        "name": "kvalita-ovzdusia",
        "resources": resources,
        "extras": [
            {"key": "identifier", "value": RECORD["dct:identifier"]},
            {"key": "endpoint", "value": f"{base}/endpoint/"},
        ],
    }


def rows_of(case: str) -> list[dict]:
    if case == "empty-datastore":
        return []
    if case == "duplicate-rows":
        return [ROWS[0], dict(ROWS[0], _id=2)]
    if case == "leaked-datastore":
        return [dict(row, **{f"{MASKED}.value": None}) for row in ROWS]
    return ROWS


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
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/endpoint/":
                self._send(200, record_of(case, base))
            elif parsed.path == "/api/3/action/package_show":
                if case == "no-dataset":
                    self._send(404, {"success": False, "error": {"message": "Not found"}})
                else:
                    self._send(200, {"success": True, "result": package_of(case, base)})
            elif parsed.path == "/api/3/action/datastore_search":
                self._send(200, {"success": True, "result": {"records": rows_of(case)}})
            elif parsed.path == "/endpoint/gone":
                self._send(404, {"error": "no such resource"})
            elif parsed.path.startswith("/endpoint/"):
                self._send(200, {"ok": True, "asked": query})
            else:
                self._send(404, {"error": self.path})

    return Handler


def run(case: str) -> set[str]:
    """The tests that failed for one case."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(case, "http://127.0.0.1:0"))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    server.RequestHandlerClass = handler(case, base)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        environment = dict(
            os.environ,
            ENDPOINT_URL=f"{base}/endpoint",
            CKAN_URL=base,
            CKAN_DATASET="kvalita-ovzdusia",
            MASKED_ATTRIBUTES=MASKED,
        )
        environment.pop("DCAT_AP_SHACL", None)
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
    # A fixture that fails is reported as ERROR, not FAILED, and a broken publication is
    # exactly the case where the fixture is what cannot be built.
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

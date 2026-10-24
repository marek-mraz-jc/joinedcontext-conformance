#!/usr/bin/env python3
"""Proof that `test_walk_analyst.py` goes red on each way the analyst's reads can be wrong (T-5537).

One process serves an Endpoint the way the platform must, then wrong in one named way at a time,
and runs the suite against each:

1. `correct` passes every case of the phase.
2. `open_before`: the Endpoint answers data before the grant.
3. `short_csv`: file.csv drops a row NGSI-LD pages out.
4. `leak`: the temporal answer carries an attribute `/access` does not grant.
5. `no_retry_after`: the 429 names no Retry-After.

    python3 tests/e2e/walk_analyst_selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROWS = 2500
LIMIT_AFTER = 40


def handler(mode: str, phase: str) -> type[BaseHTTPRequestHandler]:
    served = {"count": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _send(
            self,
            status: int,
            body: bytes,
            kind: str = "application/json",
            **headers: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = {key: values[0] for key, values in parse_qs(url.query).items()}
            if phase != "granted" and mode != "open_before":
                self._send(403, b'{"title":"Access Denied by Policy"}')
                return
            if url.path.endswith("/access"):
                grant = {
                    "actions": ["queryEntity", "queryTemporal"],
                    "attributes": ["kpiValue"],
                    "resource": {"type": "KPI"},
                }
                self._send(200, json.dumps({"permissions": [grant]}).encode())
                return
            if url.path.endswith("/file.csv"):
                rows = ROWS - 1 if mode == "short_csv" else ROWS
                body = "id,type,kpiValue.value\n" + "".join(
                    f"urn:ngsi-ld:KPI:{n},KPI,{n}\n" for n in range(rows)
                )
                self._send(200, body.encode(), "text/csv")
                return
            if url.path.endswith("/temporal/entities"):
                entity = {
                    "id": "urn:ngsi-ld:KPI:0",
                    "type": "KPI",
                    "kpiValue": [{"type": "Property", "value": 1}],
                }
                if mode == "leak":
                    entity["stewardNote"] = [{"type": "Property", "value": "hidden"}]
                self._send(200, json.dumps([entity]).encode())
                return
            if url.path.endswith("/entities"):
                served["count"] += 1
                if query.get("limit") == "1" and served["count"] > LIMIT_AFTER:
                    headers = {} if mode == "no_retry_after" else {"Retry_After": "7"}
                    self._send(429, b'{"title":"Too Many Requests"}', **headers)
                    return
                offset, limit = (
                    int(query.get("offset", "0")),
                    int(query.get("limit", "20")),
                )
                page = [
                    {
                        "id": f"urn:ngsi-ld:KPI:{n}",
                        "type": "KPI",
                        "kpiValue": {"type": "Property", "value": n},
                    }
                    for n in range(offset, min(offset + limit, ROWS))
                ]
                self._send(
                    200, json.dumps(page).encode(), NGSILD_Results_Count=str(ROWS)
                )
                return
            self._send(404, b"{}")

    return Handler


def run(mode: str, phase: str) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(mode, phase))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        **os.environ,
        "WALK_ENDPOINT_URL": f"http://127.0.0.1:{server.server_port}/api/endpoint/selftest",
        "WALK_TYPE": "KPI",
        "WALK_TOKEN": "selftest-token",
        "WALK_PHASE": phase,
        "WALK_RATE_CAP": str(LIMIT_AFTER * 3),
    }
    env.pop("WALK_REPORT", None)
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(HERE / "test_walk_analyst.py"),
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            env=env,
            capture_output=True,
            check=False,
        ).returncode
    finally:
        server.shutdown()


def main() -> int:
    cases = [
        ("correct", "before", 0),
        ("correct", "granted", 0),
        ("correct", "revoked", 0),
        ("open_before", "before", 1),
        ("short_csv", "granted", 1),
        ("leak", "granted", 1),
        ("no_retry_after", "granted", 1),
    ]
    failed = 0
    for mode, phase, expected in cases:
        code = run(mode, phase)
        verdict = "ok" if (code == 0) == (expected == 0) else "WRONG"
        failed += verdict == "WRONG"
        print(f"{verdict:5} {mode:15} {phase:8} pytest exit {code}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

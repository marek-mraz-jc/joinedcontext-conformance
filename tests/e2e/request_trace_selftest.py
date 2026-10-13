#!/usr/bin/env python3
"""Proof that `test_request_trace.py` goes red when a decision or a tenant is missing (T-3917).

One process serves two spaces and a denying Endpoint over space A, first as the platform must,
then wrong in one named way at a time, and runs the suite against each:

1. `correct` passes every case.
2. `write_then_refuse`: the denying Endpoint stores the write and answers 403 anyway (a decision
   after the store) and fails the 403 cases.
3. `open_door`: space B serves a token of space A and fails the B-URL cases.
4. `urn_routes`: an id carrying B's prefix goes to B's store whatever URL it came in on
   (a space derived from a URN segment, ADR-N-041) and fails the prefix cases.

    python3 tests/e2e/request_trace_selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
TOKENS = {"Bearer token-a": "a", "Bearer token-b": "b", "Bearer token-deny": "deny"}
B_PREFIX = "example.org:space-b"


def handler(mode: str) -> type[BaseHTTPRequestHandler]:
    store: dict[str, dict[str, dict]] = {"a": {}, "b": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _send(self, status: int, body: object | None = None, location: str = "") -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(data)

        def _door(self) -> tuple[str | None, list[str], dict[str, list[str]], bool]:
            """The space the request reaches, the path under the root, the query, and whether
            the caller may act there."""
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            caller = TOKENS.get(self.headers.get("Authorization", ""))
            # cs/{space}/ngsi-ld/v1/... or api/endpoint/deny/ngsi-ld/v1/...
            space = "a" if parts[:3] == ["api", "endpoint", "deny"] else parts[1]
            rest = [unquote(p) for p in parts[4:]] if parts[0] == "cs" else [unquote(p) for p in parts[5:]]
            allowed = caller == space or (mode == "open_door" and caller in ("a", "b"))
            return space, rest, parse_qs(url.query), allowed

        def _held(self, space: str, urn: str) -> str:
            return "b" if mode == "urn_routes" and B_PREFIX in urn else space

        def _body(self) -> dict:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

        def _refuse_or(self, allowed: bool, denied_url: bool) -> bool:
            if denied_url:
                self._send(403, {"title": "Forbidden"})
                return True
            if not allowed:
                self._send(404 if self.command == "GET" else 403)
                return True
            return False

        def do_POST(self) -> None:
            space, rest, _, allowed = self._door()
            body = self._body()
            denied = self.path.startswith("/api/endpoint/deny")
            if rest == ["subscriptions"]:
                if self._refuse_or(allowed, denied):
                    return
                return self._send(201, location=f"{self.path}/urn:ngsi-ld:Subscription:1")
            if denied and mode == "write_then_refuse":
                store[self._held(space, body["id"])][body["id"]] = body
            if self._refuse_or(allowed, denied):
                return
            store[self._held(space, body["id"])][body["id"]] = body
            self._send(201)

        def do_PATCH(self) -> None:
            space, rest, _, allowed = self._door()
            body = self._body()
            denied = self.path.startswith("/api/endpoint/deny")
            held = store[self._held(space, rest[1])].get(rest[1])
            if denied and mode == "write_then_refuse" and held:
                held.update(body)
            if self._refuse_or(allowed, denied):
                return
            if held is None:
                return self._send(404)
            held.update(body)
            self._send(204)

        def do_GET(self) -> None:
            space, rest, query, allowed = self._door()
            if self._refuse_or(allowed, self.path.startswith("/api/endpoint/deny")):
                return
            if rest[0] == "temporal":
                held = store[self._held(space, rest[2])].get(rest[2])
                return self._send(404) if held is None else self._send(200, {"id": rest[2]})
            if len(rest) == 2:
                held = store[self._held(space, rest[1])].get(rest[1])
                if held is None:
                    return self._send(404)
                return self._send(200, {"id": held["id"], "name": held["name"]["value"]})
            wanted = query.get("id", [""])[0]
            found = [{"id": e["id"]} for e in store[self._held(space, wanted)].values() if e["id"] == wanted]
            self._send(200, found)

        def do_DELETE(self) -> None:
            space, rest, _, allowed = self._door()
            if not allowed:
                return self._send(403)
            if rest[0] == "subscriptions":
                return self._send(204)
            self._send(204 if store[self._held(space, rest[1])].pop(rest[1], None) else 404)

    return Handler


def failed(mode: str) -> set[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(mode))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = {name: value for name, value in os.environ.items() if not name.startswith("TRACE_")}
    environment.update(
        TRACE_A_URL=f"{base}/cs/a/ngsi-ld/v1", TRACE_A_TOKEN="token-a",
        TRACE_B_URL=f"{base}/cs/b/ngsi-ld/v1", TRACE_B_TOKEN="token-b", TRACE_B_PREFIX=B_PREFIX,
        TRACE_DENY_URL=f"{base}/api/endpoint/deny/ngsi-ld/v1", TRACE_DENY_TOKEN="token-deny",
        TRACE_RUN="selftest",
    )
    try:
        finished = subprocess.run(
            [sys.executable, "-m", "pytest", str(HERE / "test_request_trace.py"), "-q",
             "-p", "no:cacheprovider", "--no-header", "-rfEs"],
            capture_output=True, text=True, env=environment, cwd=HERE,
        )
    finally:
        server.shutdown()
        server.server_close()
    if "passed" not in finished.stdout and "failed" not in finished.stdout:
        sys.exit(f"the suite did not run in mode {mode}:\n{finished.stdout}{finished.stderr}")
    return {
        line.split("::")[-1].split()[0]
        for line in finished.stdout.splitlines()
        if line.startswith(("FAILED", "ERROR"))
    }


#: Which cases each wrong platform must fail (at least one each).
EXPECTED = {
    "write_then_refuse": "test_a_policy_granting_none_of_the_five_refuses_with_403_and_writes_nothing",
    "open_door": "test_a_token_of_space_a_reaches_nothing_of_space_b_on_bs_url",
    "urn_routes": "test_a_urn_with_bs_prefix_on_as_url_names_nothing_of_b",
}


def main() -> int:
    problems = []
    good = failed("correct")
    if good:
        problems.append(f"the correct platform failed: {sorted(good)}")
    for mode, case in EXPECTED.items():
        broken = failed(mode)
        if not any(f.startswith(case) for f in broken):
            problems.append(f"{mode} did not fail {case} (failed: {sorted(broken)})")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"ok: correct passes; {', '.join(EXPECTED)} each fail their case")
    return 0


if __name__ == "__main__":
    sys.exit(main())

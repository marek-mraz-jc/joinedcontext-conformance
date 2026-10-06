#!/usr/bin/env python3
"""Proof that `test_urn_scope.py` tells a platform that keeps (space, URN) apart from one that
does not (T-3086, ADR-N-041).

It serves two spaces from one process, first keyed by space and id, as the platform must, then
keyed by id alone, as a store would that took a URN for global, and runs the suite against
each: the first passes, the second fails where the same URN is read, queried, patched or deleted
in the other space.

    python3 tests/e2e/urn_scope_selftest.py
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


def handler(per_space: bool) -> type[BaseHTTPRequestHandler]:
    store: dict[tuple[str, str], dict] = {}

    def key(space: str, urn: str) -> tuple[str, str]:
        return (space if per_space else "", urn)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _send(self, status: int, body: object | None = None) -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self) -> tuple[str, list[str], dict[str, list[str]]]:
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/")
            # cs / {space} / ngsi-ld / v1 / entities [/ {id} [/ attrs]]
            return parts[1], [unquote(part) for part in parts[4:]], parse_qs(url.query)

        def _body(self) -> dict:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))

        @staticmethod
        def _key_values(entity: dict) -> dict:
            out = {"id": entity["id"], "type": entity["type"]}
            for name, attribute in entity.items():
                if isinstance(attribute, dict) and "value" in attribute:
                    out[name] = attribute["value"]
            return out

        def do_POST(self) -> None:
            space, rest, _ = self._route()
            entity = self._body()
            entity.pop("@context", None)
            if key(space, entity["id"]) in store:
                return self._send(409, {"title": "AlreadyExists"})
            store[key(space, entity["id"])] = entity
            self._send(201)

        def do_GET(self) -> None:
            space, rest, query = self._route()
            if len(rest) == 2:
                entity = store.get(key(space, rest[1]))
                return self._send(404) if entity is None else self._send(200, self._key_values(entity))
            wanted = query.get("id", [None])[0]
            found = [
                self._key_values(entity)
                for (held_space, urn), entity in store.items()
                if (not per_space or held_space == space) and (wanted is None or urn == wanted)
            ]
            self._send(200, found)

        def do_PATCH(self) -> None:
            space, rest, _ = self._route()
            entity = store.get(key(space, rest[1]))
            if entity is None:
                return self._send(404)
            entity.update(self._body())
            self._send(204)

        def do_DELETE(self) -> None:
            space, rest, _ = self._route()
            self._send(204 if store.pop(key(space, rest[1]), None) is not None else 404)

    return Handler


def failed(per_space: bool) -> set[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(per_space))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("URN_")
    }
    environment.update(
        URN_SPACE_A_URL=f"{base}/cs/space-a/ngsi-ld/v1",
        URN_SPACE_B_URL=f"{base}/cs/space-b/ngsi-ld/v1",
        URN_RUN="selftest",
    )
    try:
        finished = subprocess.run(
            [sys.executable, "-m", "pytest", str(HERE / "test_urn_scope.py"), "-q",
             "-p", "no:cacheprovider", "--no-header", "-rfEs"],
            capture_output=True, text=True, env=environment, cwd=HERE,
        )
    finally:
        server.shutdown()
        server.server_close()
    return {
        line.split("::")[-1].split()[0]
        for line in finished.stdout.splitlines()
        if line.startswith(("FAILED", "ERROR"))
    }


def main() -> int:
    good = failed(per_space=True)
    broken = failed(per_space=False)
    problems = []
    if good:
        problems.append(f"a platform keyed by (space, URN) failed: {sorted(good)}")
    # Keyed by id alone, the second create of the shared URN is refused, so the fixture cannot
    # be built and every test that uses it errors: the suite cannot pass on such a store.
    if not broken:
        problems.append("a platform keyed by URN alone passed")
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"ok: (space, URN) passes; URN alone fails {len(broken)} test(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

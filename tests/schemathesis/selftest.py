#!/usr/bin/env python3
"""Proof that the T-0063 contract checks can go red (TS-09, UI-05).

The suite is only worth running against the Portal if a violation actually fails the run, so the
real schemathesis CLI runs twice against a stub Portal in this process: once against a stub that
honours docs/API/01-portal-api.md §2 (must pass, and must have answered at least one error so the
check is not vacuous) and once against a stub that answers errors as a plain-text Rust panic
(must fail, naming both checks).

    python3 selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = "ui05_error_is_problem_json,ts09_no_internal_detail_leak"

# A single operation is enough: the checks look at responses, not at the shape of the schema.
SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "portal stub", "version": "0"},
    "paths": {
        "/api/v1/projects/{project}/{plural}": {
            "get": {
                "operationId": "listResources",
                "parameters": [
                    {"name": "project", "in": "path", "required": True, "schema": {"type": "string", "minLength": 1}},
                    {"name": "plural", "in": "path", "required": True, "schema": {"type": "string", "minLength": 1}},
                ],
                "responses": {
                    "200": {
                        "description": "resource list",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "404": {
                        "description": "problem details",
                        "content": {"application/problem+json": {"schema": {"type": "object"}}},
                    },
                },
            }
        },
        # One write with nothing to generate, so every case reaches the handler: the PF-50 leg
        # needs a write the stub accepts.
        "/api/v1/projects/demo/notes": {
            "post": {
                "operationId": "createNote",
                "responses": {"201": {"description": "created"}},
            }
        },
    },
}

STATE = {"mode": "good", "errors": 0}


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102 - silence the stdlib access log
        pass

    def _dispatch(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/openapi.json":
            return self._send(200, "application/json", json.dumps(SPEC).encode())
        STATE["errors"] += 1
        if STATE["mode"] == "bad":
            # what a panicking axum handler behind a bare error layer would answer
            body = b"thread 'main' panicked at src/api/resources.rs:42:\nindex out of bounds"
            return self._send(404, "text/plain; charset=utf-8", body)
        if path == "/api/v1/projects/demo/notes":
            # a Portal that accepts this write from whoever sends it
            STATE["errors"] -= 1
            return self._send(201, "application/json", b"{}")
        segments = path.strip("/").split("/")
        if len(segments) == 5 and segments[3] == "demo":
            STATE["errors"] -= 1
            return self._send(200, "application/json", json.dumps({"items": []}).encode())
        problem = {
            "type": "https://joinedcontext.com/errors/resource-not-found",
            "title": "Resource Not Found",
            "status": 404,
            "detail": "no such resource collection",
        }
        return self._send(404, "application/problem+json", json.dumps(problem).encode())

    # every method reaches the same handler: a Portal that answers one verb as HTML and the rest
    # as problem+json would let the checks pass for the wrong reason
    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _dispatch
    do_HEAD = do_OPTIONS = do_TRACE = do_QUERY = _dispatch

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def schemathesis(port: int, workdir: str, checks: str = CHECKS, role: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("JC_SCHEMATHESIS_ROLE", None)
    if role:
        env["JC_SCHEMATHESIS_ROLE"] = role
    env["PYTHONPATH"] = HERE + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["SCHEMATHESIS_HOOKS"] = "jc_checks"
    return subprocess.run(
        [
            "schemathesis", "run",
            "--url", f"http://127.0.0.1:{port}",
            "--checks", checks,
            "--max-examples", "12",
            "--continue-on-failure",
            "--no-color",
            f"http://127.0.0.1:{port}/api/v1/openapi.json",
        ],
        env=env,
        cwd=workdir,   # schemathesis writes .schemathesis/ and .hypothesis/ into the working directory
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


VALID_ENDPOINT_SLUG = "73k2dpt2o3i5c7mve3o5w7a111"
FORBIDDEN_ENDPOINT_SLUG = "00000000000000000000000000"
UNKNOWN_ENDPOINT_SLUG = "22222222222222222222222222"

GW_STATE = {"mode": "good"}


class GatewayStub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence stdlib logging
        pass

    def _send(self, status: int, content_type: str, body: bytes, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        accept = self.headers.get("accept", "*/*")
        if_none_match = self.headers.get("if-none-match")

        def problem(status: int, title: str, detail: str) -> bytes:
            return json.dumps({
                "type": f"https://joinedcontext.com/errors/{title.lower().replace(' ', '-')}",
                "title": title,
                "status": status,
                "detail": detail,
            }).encode()

        if GW_STATE["mode"] == "bad":
            if "limit" in query:
                return self._send(
                    500, "text/plain", b"thread 'main' panicked at context-gateway/src/query.rs:18: index out of bounds"
                )
            if UNKNOWN_ENDPOINT_SLUG in path:
                return self._send(200, "application/json", b'{"ok": true}')
            if FORBIDDEN_ENDPOINT_SLUG in path:
                return self._send(403, "application/problem+json", problem(403, "Forbidden", "Slug forbidden"))
            if "schema/v1/model" in path:
                # Ignores accept (same body for all), missing strong etag
                return self._send(200, "application/json", b'{"model": "broken-stub-same-body"}')
            return self._send(200, "application/json", b'{"grants": []}')

        # Conforming mode
        # 1. a pagination parameter the surface interprets is parsed, and refused when it will not parse
        for name, values in parse_qs(query, keep_blank_values=True).items():
            if name not in ("limit", "offset"):
                continue
            if len(values) > 1 or not values[0].isdigit() or int(values[0]) > 1000:
                return self._send(400, "application/problem+json", problem(400, "Bad Request", "Malformed query parameter"))

        # 2. Slug parsing & R20 parity
        # Invalid slug forms
        segments = path.strip("/").split("/")
        # Path format: api/endpoint/{slug}/...
        if len(segments) < 3 or segments[0] != "api" or segments[1] != "endpoint":
            return self._send(404, "application/problem+json", problem(404, "Not Found", "Unknown endpoint path"))
        slug = segments[2]
        if slug in (FORBIDDEN_ENDPOINT_SLUG, UNKNOWN_ENDPOINT_SLUG):
            # Byte-identical 404 problem details
            return self._send(404, "application/problem+json", problem(404, "Resource Not Found", "Endpoint not found"))
        if slug != VALID_ENDPOINT_SLUG:
            return self._send(404, "application/problem+json", problem(404, "Resource Not Found", "Endpoint not found"))

        subpath = "/".join(segments[3:])
        if subpath.startswith("schema/v1/"):
            art = subpath.removeprefix("schema/v1/")
            if art == "model":
                if "application/x-not-a-format" in accept:
                    return self._send(406, "application/problem+json", problem(406, "Not Acceptable", "Unsupported format"))
                etag = '"model-v1-hash123"'
                headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
                if if_none_match == etag:
                    return self._send(304, "application/json", b"", headers=headers)
                if "application/yaml" in accept:
                    return self._send(200, "application/yaml; charset=utf-8", b"name: endpoint-model\nversion: 1", headers=headers)
                if "application/ld+json" in accept:
                    return self._send(200, "application/ld+json", b'{"@context": "https://schema.org", "@type": "Model"}', headers=headers)
                if "text/turtle" in accept:
                    return self._send(200, "text/turtle", b"@prefix ex: <http://example.org/> .", headers=headers)
                return self._send(200, "application/json", b'{"name": "endpoint-model", "version": 1}', headers=headers)

            artifact_bodies = {
                "schema.json": (200, "application/json", b'{"$schema": "https://json-schema.org/draft/2020-12/schema"}'),
                "context.jsonld": (200, "application/ld+json", b'{"@context": {}}'),
                "shapes.ttl": (200, "text/turtle", b"@prefix sh: <http://www.w3.org/ns/shacl#> ."),
                "model.owl": (200, "application/rdf+xml", b"<rdf:RDF></rdf:RDF>"),
                "README.md": (200, "text/markdown", b"# Model Documentation\n"),
            }
            if art in artifact_bodies:
                code, ctype, body = artifact_bodies[art]
                return self._send(code, ctype, body)
            return self._send(404, "application/problem+json", problem(404, "Resource Not Found", "Artifact not found"))

        if subpath == "access":
            if "application/odrl+json" in accept:
                return self._send(200, "application/odrl+json", b'{"@type": "odrl:Set", "permission": []}')
            if "application/vnd.joinedcontext.grant-ast+json" in accept:
                return self._send(200, "application/vnd.joinedcontext.grant-ast+json", b'{"type": "ast", "grants": []}')
            return self._send(200, "application/json", b'{"grants": [{"action": "read", "type": "Sensor"}], "allowed": true}')

        return self._send(404, "application/problem+json", problem(404, "Resource Not Found", "Path not found"))


def pytest_gateway(port: int) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = HERE + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["GATEWAY_URL"] = f"http://127.0.0.1:{port}"
    env["ENDPOINT_SLUG"] = VALID_ENDPOINT_SLUG
    env["SCHEMA_MAJOR"] = "v1"
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "-v",
            os.path.join(HERE, "test_gateway_endpoints.py"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    gw_server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayStub)
    gw_port = gw_server.server_address[1]
    threading.Thread(target=gw_server.serve_forever, daemon=True).start()

    failures = []
    workdir = tempfile.mkdtemp(prefix="jc-schemathesis-selftest-")
    try:
        # 1. Portal stub selftest
        STATE.update(mode="good", errors=0)
        good = schemathesis(port, workdir)
        if good.returncode != 0:
            failures.append(f"the conforming Portal stub failed the run:\n{good.stdout}\n{good.stderr}")
        if STATE["errors"] == 0:
            failures.append("the conforming Portal stub never answered an error, so UI-05 was never exercised")

        STATE.update(mode="bad", errors=0)
        bad = schemathesis(port, workdir)
        if bad.returncode == 0:
            failures.append(f"the panicking Portal stub passed the run:\n{bad.stdout}")
        for check in CHECKS.split(","):
            if check not in bad.stdout:
                failures.append(f"{check} did not fire on the panicking Portal stub:\n{bad.stdout}")

        # PF-50: the same accepted write fails a viewer run, naming the check, and passes a
        # steward run, so the check is neither vacuous nor always on.
        role_checks = CHECKS + ",pf50_viewer_never_writes"
        STATE.update(mode="good", errors=0)
        viewer = schemathesis(port, workdir, role_checks, role="viewer")
        if viewer.returncode == 0 or "pf50_viewer_never_writes" not in viewer.stdout:
            failures.append(f"a write the viewer got through passed the viewer run:\n{viewer.stdout}")
        steward = schemathesis(port, workdir, role_checks, role="steward")
        if steward.returncode != 0:
            failures.append(f"a steward's accepted write failed the steward run:\n{steward.stdout}")

        # 2. Gateway stub selftest (T-0064)
        GW_STATE.update(mode="good")
        gw_good = pytest_gateway(gw_port)
        if gw_good.returncode != 0:
            failures.append(f"the conforming Gateway stub failed the run:\n{gw_good.stdout}\n{gw_good.stderr}")

        GW_STATE.update(mode="bad")
        gw_bad = pytest_gateway(gw_port)
        if gw_bad.returncode == 0:
            failures.append(f"the broken Gateway stub passed the run:\n{gw_bad.stdout}")
        for required_test in [
            "test_ep49_model_content_negotiation",
            "test_ep48_schema_etag_and_caching",
            "test_ts09_malformed_pagination_parameters",
            "test_ts09_invalid_slug_permutations",
        ]:
            if required_test not in gw_bad.stdout:
                failures.append(f"{required_test} did not fail on the broken Gateway stub:\n{gw_bad.stdout}")
    finally:
        server.shutdown()
        gw_server.shutdown()

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ok: both Portal and Gateway contract checks pass conforming stubs and fail broken ones")
    return 0


if __name__ == "__main__":
    sys.exit(main())

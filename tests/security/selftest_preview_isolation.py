#!/usr/bin/env python3
"""Proof that `test_preview_isolation.py` tells an open path from a closed one (T-5548).

1. `tcp_reachable` is true for a listening port and false for a closed one, and the suite's
   dev-service case goes red when `PV_DEV_SERVICES` names a port that answers.
2. `pg_verdict`, against a TLS stub speaking the Postgres startup protocol, says ADMITTED where
   the stub asks for SCRAM and REJECTED with SQLSTATE 28000 where it answers as pg_hba does.
3. `http_status` returns the stub's 200 and 401 as numbers, so a dev URL serving the preview's
   token is a red case and not an exception.
4. `foreign_references` passes a preview pod and the mesh's identity token, and names a Secret of
   another name, an API token projection, envFrom of a ConfigMap and an automounted token.

Run it as `python3 tests/security/selftest_preview_isolation.py`; it needs no deployed system.
"""

from __future__ import annotations

import http.server
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from preview_isolation import (
    ADMITTED,
    REJECTED,
    foreign_references,
    http_status,
    parse_reply,
    pg_verdict,
    tcp_reachable,
)


def listener() -> socket.socket:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    return s


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def check_tcp() -> None:
    open_socket = listener()
    port = open_socket.getsockname()[1]
    assert tcp_reachable("127.0.0.1", port)
    assert not tcp_reachable("127.0.0.1", closed_port(), timeout=1)
    env = {**os.environ, "PV_SLUG": "selftest", "PV_IN_NAMESPACE": "1"}
    case = ["-m", "pytest", "-q", "-p", "no:cacheprovider", str(HERE / "test_preview_isolation.py"),
            "-k", "no_path_to_a_dev_service"]
    red = subprocess.run([sys.executable, *case], check=False, env={**env, "PV_DEV_SERVICES": f"127.0.0.1:{port}"},
                         capture_output=True, text=True)
    assert red.returncode == 1 and "1 failed" in red.stdout, red.stdout[-2000:]
    green = subprocess.run([sys.executable, *case], check=False, env={**env, "PV_DEV_SERVICES": f"127.0.0.1:{closed_port()}"},
                           capture_output=True, text=True)
    assert green.returncode == 0 and "1 passed" in green.stdout, green.stdout[-2000:]
    open_socket.close()


def error_response(sqlstate: str, message: str) -> bytes:
    body = b"SFATAL\0C" + sqlstate.encode() + b"\0M" + message.encode() + b"\0\0"
    return b"E" + struct.pack("!I", len(body) + 4) + body


def startup_fields(data: bytes) -> dict[str, str]:
    parts = data[8:].split(b"\0")
    return {parts[i].decode(): parts[i + 1].decode() for i in range(0, len(parts) - 1, 2) if parts[i]}


def stub_postgres(context: ssl.SSLContext, own: str) -> int:
    """A server that admits `own` to the database of its own name and refuses the rest, as pg_hba."""
    server = listener()

    def serve() -> None:
        while True:
            conn, _ = server.accept()
            with conn:
                conn.recv(8)
                conn.sendall(b"S")
                with context.wrap_socket(conn, server_side=True) as tls:
                    fields = startup_fields(tls.recv(4096))
                    if fields.get("user") == own and fields.get("database") == own:
                        tls.sendall(b"R" + struct.pack("!II", 23, 10) + b"SCRAM-SHA-256\0\0")
                    else:
                        tls.sendall(error_response("28000", f'pg_hba.conf rejects connection for user "{fields.get("user")}", database "{fields.get("database")}"'))

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def check_postgres() -> None:
    assert parse_reply(b"R" + struct.pack("!II", 8, 10)) == (ADMITTED, "10")
    verdict, detail = parse_reply(error_response("28000", "no"))
    assert verdict == REJECTED and detail == "28000 no"
    with tempfile.TemporaryDirectory() as tmp:
        key, cert = Path(tmp) / "key.pem", Path(tmp) / "cert.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=stub",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        port = stub_postgres(context, "pv_selftest")
        assert pg_verdict("127.0.0.1", port, "pv_selftest", "pv_selftest")[0] == ADMITTED
        verdict, detail = pg_verdict("127.0.0.1", port, "pv_selftest", "portal")
        assert verdict == REJECTED and detail.startswith("28000"), (verdict, detail)


def check_http() -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/open" else 401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://localhost:{server.server_address[1]}"
    assert http_status(f"{base}/open", "t") == 200
    assert http_status(f"{base}/closed", "t") == 401
    server.shutdown()


def check_references() -> None:
    linkerd = {"name": "linkerd-identity-token", "projected": {"sources": [
        {"serviceAccountToken": {"audience": "identity.l5d.io", "expirationSeconds": 86400, "path": "token"}}]}}
    good = {"metadata": {"name": "portal"}, "spec": {
        "automountServiceAccountToken": False,
        "volumes": [{"name": "tmp", "emptyDir": {}}, linkerd],
        "containers": [{"name": "portal", "env": [
            {"name": "PGPASSWORD", "valueFrom": {"secretKeyRef": {"name": "preview-credentials", "key": "pgpassword"}}}]}]}}
    assert foreign_references({"items": [good]}, {"preview-credentials"}) == []
    bad = {"metadata": {"name": "bad"}, "spec": {
        "volumes": [{"name": "s", "secret": {"secretName": "db-portal"}},
                    {"name": "api", "projected": {"sources": [{"serviceAccountToken": {"audience": "https://kubernetes.default.svc"}}]}}],
        "containers": [{"name": "c", "envFrom": [{"configMapRef": {"name": "portal-config"}}]}]}}
    found = foreign_references({"items": [bad]}, {"preview-credentials"})
    assert len(found) == 4, found
    assert any("db-portal" in f for f in found) and any("automount" in f for f in found)
    assert any("projects" in f for f in found) and any("portal-config" in f for f in found)


def main() -> int:
    for check in (check_tcp, check_postgres, check_http, check_references):
        check()
        print(f"ok   {check.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

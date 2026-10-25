"""Probes of the branch-preview isolation suite (T-5548, docs Deployment/17 section 7).

Standard library only: the suite runs as a Job inside the preview namespace, under the preview's
own NetworkPolicy, from the conformance runner image, and needs no database driver or Kubernetes
client there. `selftest_preview_isolation.py` proves each probe tells an open path from a closed one.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import urllib.error
import urllib.request

# The answers a Postgres server gives a StartupMessage, as `pg_verdict` names them.
ADMITTED = "admitted"  # pg_hba matched a line that asks for a password: the role may log in here
REJECTED = "rejected"  # pg_hba refused the role on this database before any password (SQLSTATE 28000)
NO_TLS = "no-tls"  # the server refused TLS; the cluster's pg_hba refuses every plaintext login anyway
UNREACHABLE = "unreachable"


def tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """True when a TCP connection to host:port opens within `timeout` seconds.

    Inside a meshed pod the local proxy accepts every outbound connection, so the Job that runs
    this skips the proxy on the probed ports (config.linkerd.io/skip-outbound-ports, set by
    `just dev-preview-isolation`): the answer is then the NetworkPolicy's.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def startup_message(user: str, database: str) -> bytes:
    """Protocol 3.0 StartupMessage for `user` on `database`."""
    body = struct.pack("!I", 196608)
    for key, value in (("user", user), ("database", database)):
        body += key.encode() + b"\0" + value.encode() + b"\0"
    body += b"\0"
    return struct.pack("!I", len(body) + 4) + body


def parse_reply(data: bytes) -> tuple[str, str]:
    """The server's first answer to a StartupMessage: (ADMITTED, auth code) or (REJECTED, message).

    An ErrorResponse ('E') carries fields as <type byte><text>\\0; C is the SQLSTATE, M the message.
    An Authentication request ('R') means pg_hba admitted the connection and asks for a proof.
    """
    if len(data) < 5:
        raise ValueError(f"a reply of {len(data)} bytes is no Postgres message")
    kind = data[:1]
    if kind == b"R":
        return ADMITTED, str(struct.unpack("!I", data[5:9])[0])
    if kind == b"E":
        fields = {}
        for part in data[5:].split(b"\0"):
            if part:
                fields[part[:1].decode()] = part[1:].decode(errors="replace")
        return REJECTED, f"{fields.get('C', '')} {fields.get('M', '')}".strip()
    raise ValueError(f"unexpected Postgres message type {kind!r}")


def pg_verdict(host: str, port: int, user: str, database: str, timeout: float = 5.0) -> tuple[str, str]:
    """What pg_hba says to `user` on `database`, without a password: TLS first, as the cluster requires.

    Certificate checks are off on purpose: this asks the server which pg_hba line matches, and
    sends no secret over the connection.
    """
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return UNREACHABLE, str(e)
    with raw:
        raw.sendall(struct.pack("!II", 8, 80877103))  # SSLRequest
        if raw.recv(1) != b"S":
            return NO_TLS, "the server refused TLS"
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(startup_message(user, database))
            data = b""
            while len(data) < 5 or len(data) < 1 + struct.unpack("!I", data[1:5])[0]:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                data += chunk
    return parse_reply(data)


def http_status(url: str, token: str, method: str = "GET", body: bytes | None = None, timeout: float = 10.0) -> int:
    """The status `url` answers a bearer `token` with (an HTTP error is a status, not an exception)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json, text/event-stream"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code


def client_token(token_url: str, client_id: str, client_secret: str, timeout: float = 10.0) -> str:
    """An access token of a confidential client by client_credentials."""
    from urllib.parse import urlencode

    data = urlencode({"grant_type": "client_credentials", "client_id": client_id,
                      "client_secret": client_secret}).encode()
    with urllib.request.urlopen(urllib.request.Request(token_url, data=data, method="POST"), timeout=timeout) as r:
        return json.load(r)["access_token"]


def _linkerd_identity(projected: dict) -> bool:
    """The one projection the mesh adds: a token for Linkerd's identity service, no API audience."""
    sources = projected.get("sources", [])
    return bool(sources) and all(
        set(s) == {"serviceAccountToken"} and s["serviceAccountToken"].get("audience") == "identity.l5d.io"
        for s in sources
    )


def foreign_references(pods: dict, allowed_secrets: set[str]) -> list[str]:
    """Every Secret, ConfigMap, projected token or host path the pods name beyond `allowed_secrets`.

    A pod can only mount objects of its own namespace, so what proves "no dev secret" is that the
    preview's pods name nothing but the Secrets the recipe wrote into it and carry no API token.
    """
    found = []
    for pod in pods.get("items", []):
        name = pod.get("metadata", {}).get("name", "?")
        spec = pod.get("spec", {})
        if spec.get("automountServiceAccountToken", True):
            found.append(f"{name}: automountServiceAccountToken is not false")
        for volume in spec.get("volumes", []):
            if "secret" in volume and volume["secret"].get("secretName") not in allowed_secrets:
                found.append(f"{name}: volume {volume['name']} mounts Secret {volume['secret'].get('secretName')}")
            if "projected" in volume and not _linkerd_identity(volume["projected"]):
                found.append(f"{name}: volume {volume['name']} projects {volume['projected'].get('sources')}")
            for key in ("configMap", "hostPath", "csi", "persistentVolumeClaim"):
                if key in volume:
                    found.append(f"{name}: volume {volume['name']} is a {key}")
        for container in spec.get("containers", []) + spec.get("initContainers", []):
            if container.get("name") == "linkerd-proxy":
                continue
            for source in container.get("envFrom", []):
                ref = source.get("secretRef") or source.get("configMapRef") or {}
                if ref.get("name") not in allowed_secrets:
                    found.append(f"{name}/{container['name']}: envFrom {ref.get('name')}")
            for env in container.get("env", []):
                source = env.get("valueFrom", {})
                ref = source.get("secretKeyRef") or source.get("configMapKeyRef")
                if ref is not None and ref.get("name") not in allowed_secrets:
                    found.append(f"{name}/{container['name']}: env {env['name']} reads {ref.get('name')}")
    return found

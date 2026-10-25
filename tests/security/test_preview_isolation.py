"""A branch preview cannot read dev data or secrets (T-5548, ADR-N-062, docs Deployment/17 section 7).

Two halves, because one half needs the preview's network and the other the cluster's API:

- inside the preview namespace (`PV_IN_NAMESPACE=1`): `just dev-preview-isolation <slug>` runs this
  file as a Job in pv-{slug}, under the preview's NetworkPolicy, with the preview's client secret;
  properties 1-3 (no path to dev's services, pg_hba keeps the role on its own database, dev refuses
  the preview's token);
- beside it, with `kubectl` on the dev context (`PV_KUBECTL=1`): properties 4 and 5 (the pods name
  no Secret but the preview's own, the quota refuses a 2Gi pod).

| Variable | What |
|---|---|
| `PV_SLUG` | the preview's slug: namespace pv-{slug}, role and database pv_{slug} |
| `PV_DOMAIN` | dev's domain (default `dev.joinedcontext.com`) |
| `PV_DEV_NAMESPACE` | dev's namespace (default `dev`) |
| `PV_DEV_SERVICES` | `host:port` dev services the preview must not reach (default gateway, broker, Portal) |
| `PV_DEV_DATABASES` | dev databases the preview's role must not enter (default portal, keycloak, gitea, context_broker, postgres) |
| `PV_CLIENT_SECRET` | the secret of `pv-{slug}-portal` (the Job reads it from `preview-credentials`) |
| `PV_DEV_PROTECTED_URLS` | dev URLs a dev token is served on, comma-separated (default the Portal's projects and the gateway's MCP) |
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from preview_isolation import (
    ADMITTED,
    REJECTED,
    client_token,
    foreign_references,
    http_status,
    pg_verdict,
    tcp_reachable,
)

SLUG = os.getenv("PV_SLUG", "")
DOMAIN = os.getenv("PV_DOMAIN", "dev.joinedcontext.com")
DEV = os.getenv("PV_DEV_NAMESPACE", "dev")
NAMESPACE = f"pv-{SLUG}"
ROLE = f"pv_{SLUG.replace('-', '_')}"
POSTGRES = f"postgres-cluster-rw.{DEV}.svc.cluster.local"


def _list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEV_SERVICES = _list("PV_DEV_SERVICES", ",".join(
    f"{s}.{DEV}.svc.cluster.local:8080" for s in ("context-gateway", "context-broker", "portal")))
DEV_DATABASES = _list("PV_DEV_DATABASES", "portal,keycloak,gitea,context_broker,postgres")
PROTECTED = _list("PV_DEV_PROTECTED_URLS", f"https://portal.{DOMAIN}/api/v1/projects,https://api.{DOMAIN}/api/mcp")

in_namespace = pytest.mark.skipif(not (SLUG and os.getenv("PV_IN_NAMESPACE") == "1"),
                                  reason="PV_SLUG and PV_IN_NAMESPACE=1: run inside the preview (just dev-preview-isolation)")
with_kubectl = pytest.mark.skipif(not (SLUG and os.getenv("PV_KUBECTL") == "1"),
                                  reason="PV_SLUG and PV_KUBECTL=1: run beside the preview with the dev kube context")


@in_namespace
@pytest.mark.parametrize("target", DEV_SERVICES)
def test_the_preview_has_no_path_to_a_dev_service(target):
    host, port = target.rsplit(":", 1)
    assert not tcp_reachable(host, int(port)), f"{target} answers a TCP connection from {NAMESPACE}"


@in_namespace
def test_the_preview_has_no_path_to_the_internet():
    assert not tcp_reachable("1.1.1.1", 443), f"1.1.1.1:443 answers a TCP connection from {NAMESPACE}"


@in_namespace
def test_the_preview_role_is_admitted_to_its_own_database():
    """The control: without it a closed port would pass every refusal below."""
    verdict, detail = pg_verdict(POSTGRES, 5432, ROLE, ROLE)
    assert verdict == ADMITTED, f"{ROLE} on {ROLE}: {verdict} {detail}"


@in_namespace
@pytest.mark.parametrize("database", DEV_DATABASES)
def test_the_preview_role_is_refused_every_dev_database(database):
    verdict, detail = pg_verdict(POSTGRES, 5432, ROLE, database)
    assert verdict == REJECTED and detail.startswith("28000"), f"{ROLE} on {database}: {verdict} {detail}"


@in_namespace
@pytest.mark.parametrize("url", PROTECTED)
def test_dev_refuses_the_preview_clients_token(url):
    secret = os.getenv("PV_CLIENT_SECRET", "")
    assert secret, "PV_CLIENT_SECRET is not set: the Job reads it from the Secret preview-credentials"
    token = client_token(f"https://idm.{DOMAIN}/realms/preview/protocol/openid-connect/token",
                         f"{NAMESPACE}-portal", secret)
    initialize = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "preview-isolation", "version": "1"}}}).encode()
    status = http_status(url, token, "POST", initialize) if url.endswith("/mcp") else http_status(url, token)
    assert status in (401, 403), f"{url} answered {status} to a token of realm preview"


def _kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=60, check=False)


@with_kubectl
def test_the_preview_pods_name_no_secret_but_their_own():
    result = _kubectl("-n", NAMESPACE, "get", "pods", "-o", "json")
    assert result.returncode == 0, result.stderr
    pods = json.loads(result.stdout)
    assert pods["items"], f"{NAMESPACE} runs no pod"
    assert foreign_references(pods, {"preview-credentials"}) == []


@with_kubectl
def test_the_quota_refuses_a_2gi_pod():
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "too-big", "namespace": NAMESPACE},
           "spec": {"automountServiceAccountToken": False, "securityContext": {
               "runAsNonRoot": True, "runAsUser": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
               "containers": [{"name": "c", "image": "registry.k8s.io/pause:3.10", "resources": {
                   "requests": {"memory": "2Gi"}, "limits": {"memory": "2Gi"}},
                   "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]}}
    result = subprocess.run(["kubectl", "create", "--dry-run=server", "-f", "-"], input=json.dumps(pod),
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode != 0, f"{NAMESPACE} admitted a 2Gi pod: {result.stdout}"
    assert "exceeded quota" in result.stderr or "maximum memory usage" in result.stderr, result.stderr

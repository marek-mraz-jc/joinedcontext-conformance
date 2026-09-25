"""An App's pod connects to nothing but DNS, the mesh, the gateway and what it declared (T-2839).

AP-134 and the pod half of AP-135: App A's pod cannot reach App B's pod, the database, the
cloud metadata address or anything in the cluster's private ranges, whatever its code does.
The fixtures beside this suite are the default; `APP_NETWORKPOLICY` points it at a live
`kubectl get networkpolicy -n {release}-{project}-apps -o yaml` dump of dev.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from app_egress import app_policies, egress_violations, load_documents  # noqa: E402

_FIXTURES = _HERE / "fixtures" / "app-egress"

# The reason each leaky policy has to be refused for, by its name.
WAYS_OUT = {
    "every-address": "is every address",
    "private-range": "reaches the private range 10.0.0.0/8",
    "metadata-service": "inside the private range 169.254.0.0/16",
    "no-destination": "every destination",
    "every-port": "every port",
    "neighbours": "with no namespace",
    "every-namespace": "names no one namespace",
    "a-whole-namespace": "every pod of namespace cnpg",
    "ingress-only": "no egress boundary",
}


def test_every_app_pod_reaches_only_dns_the_mesh_the_gateway_and_what_it_declared():
    override = os.getenv("APP_NETWORKPOLICY")
    path = Path(override) if override else _FIXTURES / "conforming.yaml"
    if not path.exists():
        pytest.fail(f"APP_NETWORKPOLICY does not exist: {path}")
    policies = app_policies(load_documents(path))
    assert policies, f"no NetworkPolicy labelled joinedcontext.com/app in {path}"
    violations = [v for policy in policies for v in egress_violations(policy)]
    assert not violations, "an App pod can connect outside its policy:\n" + "\n".join(violations)


@pytest.fixture(scope="module")
def leaky() -> dict[str, dict]:
    policies = app_policies(load_documents(_FIXTURES / "leaky.yaml"))
    return {p["metadata"]["name"]: p for p in policies}


def test_the_leaky_fixture_holds_every_way_out(leaky):
    assert set(leaky) == set(WAYS_OUT)


@pytest.mark.parametrize("name", sorted(WAYS_OUT))
def test_each_way_out_is_refused_with_its_reason(leaky, name):
    violations = egress_violations(leaky[name])
    assert any(WAYS_OUT[name] in v for v in violations), violations

"""Agent Runner sandbox isolation and boundary verification tests (T-0081)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from sandbox import (
    ag22_violations,
    ag26_violations,
    ag27_violations,
    ag28_violations,
    load_documents,
    workloads,
)


def _require_manifest_path() -> Path:
    env_path = os.getenv("AGENT_RUNNER_MANIFEST")
    if not env_path:
        pytest.skip("AGENT_RUNNER_MANIFEST is not set")
    p = Path(env_path)
    if not p.exists():
        pytest.fail(f"AGENT_RUNNER_MANIFEST path does not exist: {p}")
    return p


def test_the_manifest_set_holds_at_least_one_workload_and_one_networkpolicy():
    """Manifest set must contain at least one workload definition and one NetworkPolicy."""
    p = _require_manifest_path()
    docs = load_documents(p)
    w_list = workloads(docs)
    np_list = [d for d in docs if isinstance(d, dict) and d.get("kind") == "NetworkPolicy"]

    assert len(w_list) >= 1, (
        f"Manifest set at {p} contains 0 workloads; expected at least one workload"
    )
    assert len(np_list) >= 1, (
        f"Manifest set at {p} contains 0 NetworkPolicies; expected at least one NetworkPolicy"
    )


def test_ag22_runner_workload_enforces_pod_security_hardening():
    """AG-22: Agent workload must not access host filesystem, runtime sockets, or Kubernetes API."""
    docs = load_documents(_require_manifest_path())
    violations = ag22_violations(docs)
    assert not violations, "AG-22 pod security violations detected:\n" + "\n".join(violations)


def test_ag26_runner_network_policies_and_proxy_configuration():
    """AG-26: Runner workloads enforce default-deny egress with proxy routing."""
    profile = os.getenv("AGENT_RUNNER_PROFILE", "builder")
    docs = load_documents(_require_manifest_path())
    violations = ag26_violations(docs, profile=profile)
    assert not violations, (
        f"AG-26 network egress violations for profile '{profile}':\n" + "\n".join(violations)
    )


def test_ag27_builder_credentials_strictly_scoped_to_sandbox_and_app():
    """AG-27: Builder workspace must not hold context space credentials or unscoped forge tokens."""
    docs = load_documents(_require_manifest_path())
    violations = ag27_violations(docs)
    assert not violations, "AG-27 credential leakage violations detected:\n" + "\n".join(violations)


def test_ag28_workspace_ephemeral_lifecycle_and_volume_constraints():
    """AG-28: Workspaces must be ephemeral Jobs with emptyDir volumes and TTL limits."""
    docs = load_documents(_require_manifest_path())
    violations = ag28_violations(docs)
    assert not violations, "AG-28 workspace lifecycle violations detected:\n" + "\n".join(violations)

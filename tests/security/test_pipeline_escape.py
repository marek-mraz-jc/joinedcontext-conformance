"""A pipeline as a way out: the runner's egress (T-1701, PL-18, PL-23, MF-39).

The manifest half of this vector — a mapping that reads the runner's environment or its files,
a `${VAR}` naming another source's secret, a processor the platform does not ship — is refused
before anything runs, and is proved in `jc-core`
(`crates/jc-core/tests/pipeline_escape_tests.rs`, `crates/jc-core/tests/data_source_serde_tests.rs`).
What is left is the half no manifest check can hold: the `http` processor and the `http`
DataSource URL, both of which the runner dials itself. Their defence is the network, and the
network is a rendered NetworkPolicy, so it is read here.

By default the suite judges the fixtures beside it, which makes it a CI lane with no
deployment: the conforming set has to pass and the leaky one has to fail, so a green run means
the analyser still bites rather than that nothing was looked at. `PIPELINE_RUNNER_MANIFEST`
points it at a rendered chart or a `kubectl get -o yaml` dump of the real thing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from pipeline_runner import (  # noqa: E402
    PRIVATE_RANGES,
    egress_violations,
    load_documents,
    runner_policies,
    runner_workloads,
)

_FIXTURES = _HERE / "fixtures" / "pipeline-runner"


def _conforming() -> list[dict]:
    override = os.getenv("PIPELINE_RUNNER_MANIFEST")
    path = Path(override) if override else _FIXTURES / "conforming.yaml"
    if not path.exists():
        pytest.fail(f"PIPELINE_RUNNER_MANIFEST does not exist: {path}")
    return load_documents(path)


def test_the_runner_is_selected_by_a_network_policy_that_names_egress():
    """PL-23: a runner no policy selects reaches whatever the cluster's default allows."""
    docs = _conforming()
    assert runner_workloads(docs), "no runner workload in the manifest set"
    policies = runner_policies(docs)
    assert policies, "no NetworkPolicy selects the runner"
    for policy in policies:
        types = (policy.get("spec") or {}).get("policyTypes") or []
        assert "Egress" in types, f"{policy['metadata']['name']}: policyTypes without Egress"


def test_no_egress_rule_reaches_the_cluster_a_node_or_the_metadata_service():
    """PL-23, MF-39: a check fetches a URL a person typed, on this runner. That URL must not
    reach the Kubernetes API, a node, another pod or 169.254.169.254."""
    violations = egress_violations(_conforming())
    assert not violations, "the runner's egress is not default-deny:\n" + "\n".join(violations)


def test_an_open_egress_rule_is_caught():
    """Green for the right reason: with the excepts taken off the same rule, and with a rule
    that names no destination at all, the analyser has to say so."""
    violations = egress_violations(load_documents(_FIXTURES / "leaky.yaml"))
    assert violations, "the leaky manifest set passed; the analyser judges nothing"
    joined = "\n".join(violations)
    assert "169.254.0.0/16" in joined, f"the metadata range is not named:\n{joined}"
    assert "no `to`" in joined, f"a rule without a destination is not named:\n{joined}"


@pytest.mark.parametrize("private", PRIVATE_RANGES)
def test_every_private_range_the_runner_must_not_reach_is_judged(private: str):
    """A range missing from the table is a door nothing looks at, so the table is asserted
    rather than trusted: each one is caught when the rule that excepts it is opened."""
    docs = load_documents(_FIXTURES / "conforming.yaml")
    for doc in docs:
        if doc.get("kind") != "NetworkPolicy":
            continue
        for rule in (doc.get("spec") or {}).get("egress") or []:
            for destination in rule.get("to") or []:
                block = destination.get("ipBlock")
                if block and private in (block.get("except") or []):
                    block["except"] = [e for e in block["except"] if e != private]
    violations = egress_violations(docs)
    assert any(private in violation for violation in violations), (
        f"{private} opened and nothing said so: {violations}"
    )

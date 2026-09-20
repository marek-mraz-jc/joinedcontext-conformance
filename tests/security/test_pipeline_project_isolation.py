"""Pipelines of two projects share a process (T-1702, PL-07).

Project A's stream crashes the runner, fills its memory, reads `/proc`, the ConfigMap or the
environment of project B's streams. The defence is that there is nothing of B's to read: a
runner per project, its own streams ConfigMap, its own `pipeline-secrets`, its own files
volume, a CPU and memory limit of its own, and no Kubernetes API token, host namespace or
writable root to climb out of the container with.

Like its sibling suite, this reads rendered manifests — the fixtures beside it by default, the
real chart or a live dump through `PIPELINE_RUNNER_MANIFEST` — so it is a CI lane with no
cluster and the same assertions replay against dev.
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
    isolation_violations,
    load_documents,
    project_of,
    runner_workloads,
)

_FIXTURES = _HERE / "fixtures" / "pipeline-runner"


def _conforming() -> list[dict]:
    override = os.getenv("PIPELINE_RUNNER_MANIFEST")
    path = Path(override) if override else _FIXTURES / "conforming.yaml"
    if not path.exists():
        pytest.fail(f"PIPELINE_RUNNER_MANIFEST does not exist: {path}")
    return load_documents(path)


def test_no_runner_carries_two_projects_worth_of_secrets_or_files():
    """PL-07: a ConfigMap, Secret or volume two projects both read is one project reading the
    other's credentials, whatever the stream files say."""
    violations = isolation_violations(_conforming())
    assert not violations, "pipeline runners are not isolated per project:\n" + "\n".join(
        violations
    )


def test_each_runner_is_bounded_and_cannot_leave_its_container():
    """PL-07: the limits are what stops A's stream taking B's down with it, and the token, the
    host namespaces and the writable root are the three ways out of the container."""
    docs = _conforming()
    workloads = runner_workloads(docs)
    assert workloads, "no runner workload in the manifest set"
    for workload in workloads:
        pod = workload["pod_spec"]
        assert pod.get("automountServiceAccountToken") is False, (
            f"{workload['name']}: a mounted API token turns one stream into an API client"
        )
        assert not pod.get("hostNetwork") and not pod.get("hostPID") and not pod.get("hostIPC"), (
            f"{workload['name']}: a host namespace is shared"
        )
        for container in pod.get("containers") or []:
            limits = (container.get("resources") or {}).get("limits") or {}
            assert limits.get("cpu") and limits.get("memory"), (
                f"{workload['name']}/{container.get('name')}: no cpu and memory limit"
            )


def test_two_projects_on_one_runner_are_caught():
    """Green for the right reason: the leaky set is the same two projects with one Secret, one
    volume, no limits and the host's namespaces, and every one of those is named."""
    violations = isolation_violations(load_documents(_FIXTURES / "leaky.yaml"))
    joined = "\n".join(violations)
    assert violations, "the leaky manifest set passed; the analyser judges nothing"
    for expected in (
        "secretRef 'pipeline-secrets'",
        "persistentVolumeClaim 'pipeline-runner-data'",
        "hostPath",
        "hostPID",
        "automountServiceAccountToken",
        "no cpu limit",
        "no memory limit",
        "readOnlyRootFilesystem",
    ):
        assert expected in joined, f"{expected!r} is not named:\n{joined}"


def test_a_runner_is_attributed_to_exactly_one_project():
    """The whole suite rests on knowing whose runner a workload is; a manifest set where two
    runners answer to the same project name would hide a shared one."""
    workloads = runner_workloads(_conforming())
    projects = [project_of(workload["doc"]) for workload in workloads]
    assert all(projects), f"a runner with no project: {projects}"
    assert len(set(projects)) == len(projects), f"two runners claim one project: {projects}"

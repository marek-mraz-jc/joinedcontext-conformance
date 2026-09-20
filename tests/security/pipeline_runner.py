"""Pipeline runner isolation and egress, read from rendered manifests (T-1701, T-1702).

Two properties live here, both of them about a runner nobody can reach from outside and that
reaches almost nothing itself:

* **PL-23, T-1701** — the runner's egress is default-deny. It reaches the Context Gateway, the
  cluster's DNS and public addresses; every private range is out, because a `check` fetches a
  `DataSource` URL a person typed on this very runner (MF-39) and that URL must not reach the
  Kubernetes API, a node, a webhook, another pod or a cloud metadata service.
* **PL-07, T-1702** — two projects never share a runner. Separate workloads, and no volume,
  ConfigMap, Secret or `envFrom` in common; each runner bounded by its own CPU and memory
  limits, so one project's stream cannot take the node down with it; and no Kubernetes API
  token, host namespace or writable root to climb out of.

The functions are pure: they take parsed manifests and return the list of violations, each one
naming the object and what is wrong with it. That keeps the suite runnable in CI over a
rendered chart with no cluster, and runnable again over a live `kubectl get -o yaml` dump.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import yaml

#: The addresses a runner must never be able to open a connection to. A rule that opens
#: `0.0.0.0/0` carries every one of them in its `except` list or it is not default-deny:
#: private space (RFC 1918), the loopback of the node, and the link-local range that holds
#: 169.254.169.254, the address a cloud hands a node's credentials out on.
PRIVATE_RANGES: tuple[str, ...] = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "127.0.0.0/8",
)

_WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "Pod", "CronJob")
_RUNNER_HINTS = ("pipeline-runner", "pipeline-scheduled")


def load_documents(path: str | Path) -> list[dict]:
    """Every YAML document under `path`, which is one file or a directory of them."""
    p = Path(path)
    if not p.exists():
        return []
    files = sorted(p.glob("**/*.yaml")) + sorted(p.glob("**/*.yml")) if p.is_dir() else [p]
    documents: list[dict] = []
    for f in files:
        if not f.is_file():
            continue
        for doc in yaml.safe_load_all(f.read_text(encoding="utf-8")):
            if isinstance(doc, dict) and doc:
                documents.append(doc)
    return documents


def _pod_spec(doc: dict) -> dict:
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        job = (spec.get("jobTemplate") or {}).get("spec") or {}
        return (job.get("template") or {}).get("spec") or {}
    return (spec.get("template") or {}).get("spec") or {}


def _labels(doc: dict) -> dict:
    return (doc.get("metadata") or {}).get("labels") or {}


def project_of(doc: dict) -> str:
    """The project a manifest belongs to.

    An explicit `joinedcontext.com/project` label wins; otherwise the Helm release
    (`app.kubernetes.io/instance`), which is one per project, and otherwise the namespace. The
    order matters only for a manifest set that carries more than one project, which is exactly
    the set this module exists to judge.
    """
    labels = _labels(doc)
    return (
        labels.get("joinedcontext.com/project")
        or labels.get("app.kubernetes.io/instance")
        or (doc.get("metadata") or {}).get("namespace")
        or "unknown"
    )


def _is_runner(name: str, labels: dict) -> bool:
    haystack = " ".join(
        [name, str(labels.get("app.kubernetes.io/name", "")), str(labels.get("app.kubernetes.io/instance", ""))]
    )
    return any(hint in haystack for hint in _RUNNER_HINTS)


def runner_workloads(docs: list[dict]) -> list[dict]:
    """Every workload that runs a pipeline: the resident runner and the scheduled Jobs."""
    found = []
    for doc in docs:
        if doc.get("kind") not in _WORKLOAD_KINDS:
            continue
        name = (doc.get("metadata") or {}).get("name", "")
        if not _is_runner(name, _labels(doc)):
            continue
        found.append(
            {
                "kind": doc["kind"],
                "name": name,
                "project": project_of(doc),
                "pod_spec": _pod_spec(doc),
                "doc": doc,
            }
        )
    return found


def runner_policies(docs: list[dict]) -> list[dict]:
    """Every NetworkPolicy that selects a runner pod, by its `app.kubernetes.io` labels.

    A policy that selects something else — the Portal's ingress rule for the runner's captured
    test output, say — is another object's boundary and is judged where that object is.
    """
    policies = []
    for doc in docs:
        if doc.get("kind") != "NetworkPolicy":
            continue
        selector = ((doc.get("spec") or {}).get("podSelector") or {}).get("matchLabels") or {}
        name = (doc.get("metadata") or {}).get("name", "")
        if _is_runner("", selector) or (_is_runner(name, {}) and not selector):
            policies.append(doc)
    return policies


def _except_covers(cidr: str, excepted: list[str]) -> bool:
    network = ipaddress.ip_network(cidr, strict=False)
    return any(network.subnet_of(ipaddress.ip_network(e, strict=False)) for e in excepted)


def egress_violations(docs: list[dict]) -> list[str]:
    """PL-23, T-1701: default-deny egress, and no private address behind an open rule."""
    violations: list[str] = []
    workloads = runner_workloads(docs)
    policies = runner_policies(docs)

    if not workloads:
        return ["no pipeline runner workload in the manifest set; there is nothing to judge"]
    if not policies:
        return ["no NetworkPolicy selects a pipeline runner pod; egress is whatever the cluster allows"]

    covered = set()
    for policy in policies:
        name = (policy.get("metadata") or {}).get("name", "unknown")
        spec = policy.get("spec") or {}
        types = spec.get("policyTypes") or []
        if "Egress" not in types:
            violations.append(f"NetworkPolicy '{name}': policyTypes does not carry Egress")
        if "Ingress" not in types:
            violations.append(
                f"NetworkPolicy '{name}': policyTypes does not carry Ingress; nothing calls a "
                "runner, so its ingress is closed by naming it (PL-07)"
            )
        if spec.get("ingress"):
            violations.append(
                f"NetworkPolicy '{name}': an ingress rule opens a door into the runner"
            )
        covered.update(
            str(v) for v in ((spec.get("podSelector") or {}).get("matchLabels") or {}).values()
        )
        for index, rule in enumerate(spec.get("egress") or []):
            where = f"NetworkPolicy '{name}' egress[{index}]"
            destinations = rule.get("to") or []
            if not destinations:
                violations.append(f"{where}: no `to`, so the rule opens every address")
                continue
            for destination in destinations:
                block = destination.get("ipBlock") or {}
                cidr = block.get("cidr")
                if not cidr:
                    continue
                excepted = [str(e) for e in block.get("except") or []]
                try:
                    reachable = [
                        private
                        for private in PRIVATE_RANGES
                        if _reaches(cidr, private) and not _except_covers(private, excepted)
                    ]
                except ValueError as error:
                    violations.append(f"{where}: cidr '{cidr}' is not an address range ({error})")
                    continue
                for private in reachable:
                    violations.append(
                        f"{where}: cidr '{cidr}' reaches {private}, which holds the cluster's "
                        "own pods, its nodes or the metadata service (PL-23, MF-39)"
                    )

    for workload in workloads:
        if not any(
            label in covered
            for label in _labels(workload["doc"]).values()
        ) and not covered:
            violations.append(
                f"{workload['name']} ({workload['kind']}): no NetworkPolicy selects it"
            )
    return violations


def _reaches(cidr: str, private: str) -> bool:
    """Whether a rule's `cidr` contains any address of `private`."""
    allowed = ipaddress.ip_network(cidr, strict=False)
    target = ipaddress.ip_network(private, strict=False)
    if allowed.version != target.version:
        return False
    return allowed.overlaps(target)


def isolation_violations(docs: list[dict]) -> list[str]:
    """PL-07, T-1702: one runner per project, bounded, and with no way out of its container."""
    violations: list[str] = []
    workloads = runner_workloads(docs)
    if not workloads:
        return ["no pipeline runner workload in the manifest set; there is nothing to judge"]

    shared: dict[tuple[str, str], set[str]] = {}
    for workload in workloads:
        ident = f"{workload['name']} ({workload['kind']})"
        project = workload["project"]
        pod = workload["pod_spec"]
        containers = list(pod.get("containers") or []) + list(pod.get("initContainers") or [])
        if not containers:
            violations.append(f"{ident}: no container, so nothing here runs the project's streams")
            continue

        if pod.get("hostNetwork") is True:
            violations.append(f"{ident}: hostNetwork puts the runner on the node's network")
        if pod.get("hostPID") is True:
            violations.append(f"{ident}: hostPID shows the runner every process on the node")
        if pod.get("hostIPC") is True:
            violations.append(f"{ident}: hostIPC shares memory with everything on the node")
        if pod.get("automountServiceAccountToken") is not False:
            violations.append(
                f"{ident}: automountServiceAccountToken is not false, so a mapping that reads a "
                "file reads a Kubernetes API token"
            )

        for volume in pod.get("volumes") or []:
            if "hostPath" in volume:
                violations.append(
                    f"{ident}: volume '{volume.get('name')}' is a hostPath into the node"
                )
            for key, name in (
                ("persistentVolumeClaim", "claimName"),
                ("configMap", "name"),
                ("secret", "secretName"),
            ):
                source = volume.get(key)
                if isinstance(source, dict) and source.get(name):
                    shared.setdefault((key, str(source[name])), set()).add(project)

        pod_security = pod.get("securityContext") or {}
        for container in containers:
            c_name = container.get("name", "unknown")
            security = container.get("securityContext") or {}
            if security.get("privileged") is True:
                violations.append(f"{ident} container '{c_name}': privileged is true")
            if security.get("allowPrivilegeEscalation") is not False:
                violations.append(
                    f"{ident} container '{c_name}': allowPrivilegeEscalation is not false"
                )
            if security.get("readOnlyRootFilesystem") is not True:
                violations.append(
                    f"{ident} container '{c_name}': readOnlyRootFilesystem is not true"
                )
            non_root = security.get("runAsNonRoot", pod_security.get("runAsNonRoot"))
            if non_root is not True:
                violations.append(f"{ident} container '{c_name}': runAsNonRoot is not true")
            dropped = [str(cap).upper() for cap in (security.get("capabilities") or {}).get("drop", [])]
            if "ALL" not in dropped:
                violations.append(
                    f"{ident} container '{c_name}': capabilities.drop does not carry ALL"
                )

            limits = (container.get("resources") or {}).get("limits") or {}
            for resource in ("cpu", "memory"):
                if not limits.get(resource):
                    violations.append(
                        f"{ident} container '{c_name}': no {resource} limit, so one project's "
                        "stream can take the node from every other (PL-07)"
                    )

            for source in container.get("envFrom") or []:
                for key, name in (("secretRef", "name"), ("configMapRef", "name")):
                    reference = source.get(key)
                    if isinstance(reference, dict) and reference.get(name):
                        shared.setdefault((key, str(reference[name])), set()).add(project)

    for (kind, name), projects in sorted(shared.items()):
        if len(projects) > 1:
            violations.append(
                f"{kind} '{name}' is read by the runners of {', '.join(sorted(projects))}: two "
                "projects share one set of credentials or files (PL-07)"
            )
    return violations

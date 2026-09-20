"""Agent Runner Kubernetes sandbox manifest compliance validator (T-0081).

Verifies AG-22 (privilege isolation), AG-26 (network egress), AG-27 (credential scoping),
and AG-28 (workspace ephemerality) across rendered Kubernetes manifests.
"""

from __future__ import annotations

import re
from pathlib import Path
import yaml

_FORBIDDEN_CREDENTIALS = re.compile(
    r"^(GATEWAY_TOKEN|JC_GATEWAY_TOKEN|SPACE_TOKEN|KUBECONFIG|AWS_.*|.*_ADMIN_.*)$"
)
_RUNTIME_SOCKETS = ("docker.sock", "containerd.sock", "crio.sock", "cri.sock")


def load_documents(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []

    files = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")) if p.is_dir() else [p]
    documents: list[dict] = []
    for f in files:
        if not f.is_file():
            continue
        for doc in yaml.safe_load_all(f.read_text(encoding="utf-8")):
            if doc and isinstance(doc, dict):
                documents.append(doc)
    return documents


def workloads(docs: list[dict]) -> list[dict]:
    workload_list: list[dict] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "unknown")
        if kind in ("Deployment", "StatefulSet", "Job", "DaemonSet"):
            pod_spec = doc.get("spec", {}).get("template", {}).get("spec") or {}
            workload_list.append({"kind": kind, "name": name, "pod_spec": pod_spec, "doc": doc})
        elif kind == "CronJob":
            job_spec = doc.get("spec", {}).get("jobTemplate", {}).get("spec", {})
            pod_spec = job_spec.get("template", {}).get("spec") or {}
            workload_list.append({"kind": kind, "name": name, "pod_spec": pod_spec, "doc": doc})
        elif kind == "Pod":
            pod_spec = doc.get("spec") or {}
            workload_list.append({"kind": kind, "name": name, "pod_spec": pod_spec, "doc": doc})
    return workload_list


def ag22_violations(docs: list[dict]) -> list[str]:
    violations: list[str] = []
    wl_list = workloads(docs)
    for wl in wl_list:
        name = wl["name"]
        kind = wl["kind"]
        pod_spec = wl["pod_spec"]
        ident = f"{name} ({kind})"

        containers = pod_spec.get("containers", []) + pod_spec.get("initContainers", [])
        if not containers:
            violations.append(f"{ident}: workload declares no containers")
            continue

        for vol in pod_spec.get("volumes", []):
            if "hostPath" in vol:
                violations.append(f"{ident}: volume '{vol.get('name')}' uses forbidden hostPath")

        for c in containers:
            c_name = c.get("name", "unknown")
            for m in c.get("volumeMounts", []):
                mp = m.get("mountPath", "")
                if any(sock in mp for sock in _RUNTIME_SOCKETS):
                    violations.append(
                        f"{ident} container '{c_name}': volumeMount '{mp}' mounts container runtime socket"
                    )

        if pod_spec.get("hostNetwork") is True:
            violations.append(f"{ident}: hostNetwork is enabled")
        if pod_spec.get("hostPID") is True:
            violations.append(f"{ident}: hostPID is enabled")
        if pod_spec.get("hostIPC") is True:
            violations.append(f"{ident}: hostIPC is enabled")

        if pod_spec.get("automountServiceAccountToken") is not False:
            violations.append(f"{ident}: automountServiceAccountToken is not false")

        pod_sc = pod_spec.get("securityContext", {})
        for c in containers:
            c_name = c.get("name", "unknown")
            c_sc = c.get("securityContext", {})

            if c_sc.get("privileged") is True:
                violations.append(f"{ident} container '{c_name}': privileged is true")

            if c_sc.get("allowPrivilegeEscalation") is not False:
                violations.append(
                    f"{ident} container '{c_name}': allowPrivilegeEscalation is not false"
                )

            if c_sc.get("readOnlyRootFilesystem") is not True:
                violations.append(
                    f"{ident} container '{c_name}': readOnlyRootFilesystem is not true"
                )

            run_as_non_root = (
                c_sc.get("runAsNonRoot")
                if "runAsNonRoot" in c_sc
                else pod_sc.get("runAsNonRoot")
            )
            if run_as_non_root is not True:
                violations.append(f"{ident} container '{c_name}': runAsNonRoot is not true")

            caps = c_sc.get("capabilities", {})
            dropped = [str(cap).upper() for cap in caps.get("drop", [])]
            if "ALL" not in dropped:
                violations.append(
                    f"{ident} container '{c_name}': capabilities.drop does not contain 'ALL'"
                )

    return violations


def ag26_violations(docs: list[dict], profile: str = "builder") -> list[str]:
    violations: list[str] = []
    wl_list = workloads(docs)
    netpols = [d for d in docs if isinstance(d, dict) and d.get("kind") == "NetworkPolicy"]

    if not netpols:
        return ["no NetworkPolicy found in manifests; default-deny egress required"]

    for wl in wl_list:
        ident = f"{wl['name']} ({wl['kind']})"
        has_egress_policy = False
        for np in netpols:
            pts = np.get("spec", {}).get("policyTypes", [])
            if "Egress" in pts:
                has_egress_policy = True
                break
        if not has_egress_policy:
            violations.append(f"{ident}: missing NetworkPolicy with Egress policyType")

    for np in netpols:
        np_name = np.get("metadata", {}).get("name", "unknown")
        spec = np.get("spec", {})
        for rule in spec.get("egress", []):
            for dest in rule.get("to", []):
                cidr = dest.get("ipBlock", {}).get("cidr", "")
                if cidr in ("0.0.0.0/0", "::/0"):
                    violations.append(
                        f"NetworkPolicy '{np_name}': egress allows 0.0.0.0/0 (unrestricted egress)"
                    )

            if profile == "steward":
                for dest in rule.get("to", []):
                    if dest.get("namespaceSelector") == {} and not dest.get("podSelector"):
                        for p in rule.get("ports", []):
                            if p.get("port") in (443, "443", "https"):
                                violations.append(
                                    f"NetworkPolicy '{np_name}': egress allows port 443 to any namespace"
                                )
                if not rule.get("to") and rule.get("ports"):
                    for p in rule.get("ports", []):
                        if p.get("port") in (443, "443", "https"):
                            violations.append(
                                f"NetworkPolicy '{np_name}': egress allows port 443 without destination constraint"
                            )

    if profile == "builder":
        for wl in wl_list:
            ident = f"{wl['name']} ({wl['kind']})"
            containers = wl["pod_spec"].get("containers", []) + wl["pod_spec"].get(
                "initContainers", []
            )
            for c in containers:
                c_name = c.get("name", "unknown")
                envs = {
                    e.get("name"): str(e.get("value", ""))
                    for e in c.get("env", [])
                    if isinstance(e, dict) and "name" in e
                }
                if "HTTPS_PROXY" not in envs and "HTTP_PROXY" not in envs:
                    violations.append(
                        f"{ident} container '{c_name}': missing HTTPS_PROXY/HTTP_PROXY pointing at egress proxy"
                    )
                if envs.get("NO_PROXY") == "*":
                    violations.append(
                        f"{ident} container '{c_name}': NO_PROXY is '*' which neuters egress proxy"
                    )

    return violations


def ag27_violations(docs: list[dict]) -> list[str]:
    violations: list[str] = []
    for wl in workloads(docs):
        ident = f"{wl['name']} ({wl['kind']})"
        doc = wl["doc"]
        pod_spec = wl["pod_spec"]
        containers = pod_spec.get("containers", []) + pod_spec.get("initContainers", [])

        has_forge_token = False
        for c in containers:
            c_name = c.get("name", "unknown")
            for e in c.get("env", []):
                if not isinstance(e, dict):
                    continue
                var_name = e.get("name", "")
                if _FORBIDDEN_CREDENTIALS.match(var_name):
                    violations.append(
                        f"{ident} container '{c_name}': forbidden credential '{var_name}'"
                    )

                if (
                    ("TOKEN" in var_name or "SECRET" in var_name or "KEY" in var_name)
                    and "value" in e
                    and not e.get("valueFrom")
                ):
                    violations.append(
                        f"{ident} container '{c_name}': literal credential in env.value for '{var_name}' (secretRef only required)"
                    )

                val_from = e.get("valueFrom", {})
                if "secretKeyRef" in val_from:
                    s_name = val_from["secretKeyRef"].get("name", "")
                    s_key = val_from["secretKeyRef"].get("key", "")
                    if _FORBIDDEN_CREDENTIALS.match(s_name) or _FORBIDDEN_CREDENTIALS.match(s_key):
                        violations.append(
                            f"{ident} container '{c_name}': secretKeyRef references forbidden credential '{s_name}/{s_key}'"
                        )

                if "FORGE_TOKEN" in var_name:
                    has_forge_token = True

            for ef in c.get("envFrom", []):
                s_ref = ef.get("secretRef", {}).get("name", "")
                if _FORBIDDEN_CREDENTIALS.match(s_ref):
                    violations.append(
                        f"{ident} container '{c_name}': envFrom secretRef references forbidden credential '{s_ref}'"
                    )

        if has_forge_token:
            doc_annos = doc.get("metadata", {}).get("annotations", {}) or {}
            pod_annos = (
                pod_spec.get("metadata", {}).get("annotations", {})
                if isinstance(pod_spec.get("metadata"), dict)
                else {}
            )
            tpl_annos = (
                doc.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {})
                or {}
            )
            annos = {**doc_annos, **pod_annos, **tpl_annos}
            scope = (
                annos.get("forge.joinedcontext.org/scope")
                or annos.get("forge-token-scope")
                or annos.get("scope")
            )
            if not scope:
                violations.append(
                    f"{ident}: forge token present but missing declared scope annotation"
                )
            elif not scope.startswith("apps/"):
                violations.append(f"{ident}: forge token scope '{scope}' is wider than 'apps/'")

    return violations


def ag28_violations(docs: list[dict]) -> list[str]:
    violations: list[str] = []
    for wl in workloads(docs):
        ident = f"{wl['name']} ({wl['kind']})"
        kind = wl["kind"]
        doc = wl["doc"]
        pod_spec = wl["pod_spec"]

        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            violations.append(
                f"{ident}: {kind} used as runner workspace; only ephemeral Job permitted"
            )

        if kind == "Job":
            spec = doc.get("spec", {})
            if "ttlSecondsAfterFinished" not in spec:
                violations.append(
                    f"{ident}: missing ttlSecondsAfterFinished (TTL required for cleanup)"
                )
            if "activeDeadlineSeconds" not in spec:
                violations.append(f"{ident}: missing activeDeadlineSeconds")

        if pod_spec.get("restartPolicy") == "Always":
            violations.append(f"{ident}: restartPolicy is 'Always' (must be Never or OnFailure)")

        for vol in pod_spec.get("volumes", []):
            vol_name = vol.get("name", "")
            if "persistentVolumeClaim" in vol:
                violations.append(
                    f"{ident}: volume '{vol_name}' is a PersistentVolumeClaim (must be emptyDir)"
                )
            if "hostPath" in vol:
                violations.append(
                    f"{ident}: volume '{vol_name}' is a hostPath (must be emptyDir)"
                )

    return violations

"""What an App's pod may connect to (T-2839, AP-134, AP-135).

The Portal gives every App pod one NetworkPolicy: DNS, the Linkerd control plane, the context
gateway's pods, and each network the manifest declared with the private ranges cut out. This
judges that policy from a rendered file or a live `kubectl get networkpolicy -o yaml` dump, so
the same assertions run in CI and against dev.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import yaml

APP_LABEL = "joinedcontext.com/app"
NAMESPACE_NAME = "kubernetes.io/metadata.name"
# The same list the Portal cuts out of a declared network (src/apps/reconciler.rs).
PRIVATE_RANGES = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
    )
)
# The one namespace a peer may be named by alone: the mesh's control plane, whose pods carry no
# label worth pinning and which the proxy must reach before the pod can start.
WHOLE_NAMESPACE_PEERS = ("linkerd",)


def load_documents(path: str | Path) -> list[dict]:
    """Every object in a file or a directory of files; a `kubectl get -o yaml` List is unrolled."""
    p = Path(path)
    files = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")) if p.is_dir() else [p]
    documents: list[dict] = []
    for file in files:
        for doc in yaml.safe_load_all(file.read_text(encoding="utf-8")):
            if not isinstance(doc, dict):
                continue
            documents.extend(i for i in doc.get("items") or [] if isinstance(i, dict))
            if doc.get("kind") != "List":
                documents.append(doc)
    return documents


def app_policies(docs: list[dict]) -> list[dict]:
    return [
        d
        for d in docs
        if d.get("kind") == "NetworkPolicy"
        and (d.get("metadata", {}).get("labels") or {}).get(APP_LABEL) == "true"
    ]


def _block_violations(where: str, block: dict) -> list[str]:
    try:
        network = ipaddress.ip_network(str(block.get("cidr")), strict=True)
    except ValueError:
        return [f"{where}: ipBlock {block.get('cidr')!r} is not a network"]
    if network.prefixlen == 0:
        return [f"{where}: ipBlock {network} is every address"]
    excepted = set()
    for text in block.get("except") or []:
        try:
            excepted.add(ipaddress.ip_network(str(text), strict=True))
        except ValueError:
            return [f"{where}: except {text!r} is not a network"]
    found = []
    for private in PRIVATE_RANGES:
        if private.version != network.version:
            continue
        if network.subnet_of(private):
            found.append(f"{where}: ipBlock {network} lies inside the private range {private}")
        elif private.subnet_of(network) and not any(private.subnet_of(e) for e in excepted):
            found.append(f"{where}: ipBlock {network} reaches the private range {private}")
    return found


def _peer_violations(where: str, peer: dict) -> list[str]:
    if "ipBlock" in peer:
        return _block_violations(where, peer["ipBlock"])
    namespaces = peer.get("namespaceSelector")
    if namespaces is None:
        return [f"{where}: a pod peer with no namespace reaches the App's neighbours"]
    namespace = (namespaces.get("matchLabels") or {}).get(NAMESPACE_NAME)
    if not namespace or namespaces.get("matchExpressions"):
        return [f"{where}: the namespace selector names no one namespace"]
    pods = (peer.get("podSelector") or {}).get("matchLabels")
    if not pods and namespace not in WHOLE_NAMESPACE_PEERS:
        return [f"{where}: every pod of namespace {namespace}"]
    return []


def egress_violations(policy: dict) -> list[str]:
    """Each way the policy lets the pod connect beyond DNS, the mesh, the gateway and its
    declared public networks; empty when there is none."""
    name = f"{policy.get('metadata', {}).get('namespace')}/{policy.get('metadata', {}).get('name')}"
    spec = policy.get("spec") or {}
    if "Egress" not in (spec.get("policyTypes") or []):
        return [f"{name}: no egress boundary, the pod may connect anywhere"]
    found = []
    for index, rule in enumerate(spec.get("egress") or []):
        where = f"{name} egress[{index}]"
        peers = rule.get("to")
        if not peers:
            found.append(f"{where}: no destination named, so every destination")
        if not rule.get("ports"):
            found.append(f"{where}: no port named, so every port")
        for peer in peers or []:
            found.extend(_peer_violations(where, peer))
    return found

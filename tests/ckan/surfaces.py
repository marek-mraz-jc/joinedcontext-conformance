"""Does every published Endpoint's CKAN dataset list every surface it serves, and does each answer
(T-3888, EP-108, EP-109, EP-110, EP-69)?

For every Endpoint of the projects named, read from the Portal (`GET /api/v1/projects/{project}/
endpoints`):

- **listed**: an Endpoint with `spec.publish.ckan` has a dataset carrying one resource per surface
  it serves (its `enabledRepresentations`, or every standard but `sta` less its
  `disabledRepresentations`; NGSI-LD, GeoJSON and MCP always), no surface resource it does not
  serve, and a usage description on each; no resource URL carries a query, a token or a key;
- **private**: an Endpoint without `spec.publish.ckan` has no dataset, and one that is not public
  is never a public dataset;
- **answers**: each surface resource answers over its own URL: HTTP `GET` 200 (401 for a restricted
  Endpoint, the policy working), a WebSocket upgrade whose `x-subscribe` answers 201, an MCP
  `initialize`;
- **current** (`--write`): one entity written through a writable Endpoint, then the seconds until
  its DataStore row holds the written value and the dataset's `metadata_modified` moved, as p50
  and p95 over `--samples` writes.

    python3 tests/ckan/surfaces.py --portal https://portal.dev.joinedcontext.com \\
        --ckan https://data.dev.joinedcontext.com helsinki banskabystrica

prints one table per Endpoint and exits 1 on any FAIL. `PORTAL_TOKEN` reads the endpoint list,
`JC_GATEWAY_TOKEN` (optional) opens restricted surfaces and `JC_WRITER_TOKEN` writes the probe
entity; none is printed. Only `--write` writes, and only the one probe entity it names.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import requests
from completeness import Reader, Report
from mcp_check import PROTOCOL, rpc

TIMEOUT = 60

#: Representation → (path under the Endpoint, CKAN format), as `jcctl publish ckan` writes them
#: (platform crates/jcctl/src/publish/ckan.rs `distribution`).
SURFACES = {
    "ngsi-ld": ("/ngsi-ld/v1/", "NGSI-LD"),
    "websocket": ("/ws", "WebSocket"),
    "mcp": ("/mcp", "MCP"),
    "ogc-features": ("/ogc/features/", "OGCFEAT"),
    "odata": ("/odata/v4/", "OData"),
    "sta": ("/sta/v1.1/", "STA"),
    "geojson": ("/file.geojson", "GeoJSON"),
    "csv": ("/file.csv", "CSV"),
    "xlsx": ("/file.xlsx", "XLSX"),
    "json": ("/file.json", "JSON"),
    "zip": ("/file.zip", "ZIP"),
}
#: A credential where a URL or a text should name none (EP-110).
SECRET = re.compile(r"(?i)(?:[?&](?:token|access_token|apikey|api_key|key)=|bearer[ .]+(?!\$TOKEN\b)[A-Za-z0-9._~+/=-]{16,}|\beyJ[A-Za-z0-9_-]{8,}\.)")


def served(spec: dict[str, Any]) -> set[str]:
    """The representations an Endpoint serves (jc-core `EndpointSpec::served_representations`)."""
    mcp_on = spec.get("mcp") is not False
    listed = spec.get("enabledRepresentations")
    if listed is not None:
        surfaces = set(listed)
    else:
        disabled = set(spec.get("disabledRepresentations") or [])
        surfaces = {name for name in SURFACES if name != "sta" and name not in disabled}
        if not mcp_on:
            surfaces.discard("mcp")
    surfaces |= {"ngsi-ld", "geojson"} | ({"mcp"} if mcp_on else set())
    return surfaces


def dataset_name(manifest: dict[str, Any]) -> str | None:
    """The CKAN dataset an Endpoint publishes as, or None when it declares no publication."""
    ckan = ((manifest.get("spec") or {}).get("publish") or {}).get("ckan")
    if not isinstance(ckan, dict):
        return None
    return ckan.get("name") or manifest["metadata"]["name"]


def endpoint_url(manifest: dict[str, Any], base: str) -> str:
    url = (manifest.get("status") or {}).get("url") or f"{base.rstrip('/')}/api/endpoint/{manifest['spec']['slug']}"
    return url.rstrip("/")


def compare(spec: dict[str, Any], url: str, package: dict[str, Any] | None) -> list[str]:
    """What is wrong between an Endpoint and the dataset CKAN holds for it (EP-108, EP-110, EP-69).

    `package` is the `package_show` result, None when CKAN has no such dataset."""
    if package is None:
        return ["no dataset"]
    problems = []
    if spec.get("audience") != "public" and not package.get("private"):
        problems.append("a restricted endpoint is a public dataset")
    resources = package.get("resources") or []
    by_url = {(r.get("url") or "").split("?", 1)[0]: r for r in resources}
    expected = served(spec)
    for name, (path, form) in SURFACES.items():
        resource = by_url.get(url + path)
        if name in expected and resource is None:
            problems.append(f"{name}: no resource at {path}")
        elif name not in expected and resource is not None:
            problems.append(f"{name}: listed and not served")
        elif resource is not None:
            if resource.get("format") != form:
                problems.append(f"{name}: format {resource.get('format')!r}, not {form!r}")
            if not (resource.get("description") or "").strip():
                problems.append(f"{name}: no usage description")
    for resource in resources:
        address = resource.get("url") or ""
        if SECRET.search(address) or SECRET.search(resource.get("description") or ""):
            problems.append(f"{resource.get('name')}: carries a credential")
        elif "?" in address and not resource.get("datastore_active"):
            problems.append(f"{resource.get('name')}: URL carries a query")
    return problems


def answers_http(reader: Reader, url: str, public: bool) -> str | None:
    """None when the surface answers as its audience says, else what it answered."""
    try:
        answer = reader.http.get(url, headers=_auth(reader), timeout=TIMEOUT, stream=True)
    except requests.RequestException as error:
        return type(error).__name__
    answer.close()
    allowed = {200} if public or reader.bearer else {200, 401}
    return None if answer.status_code in allowed else f"answered {answer.status_code}"


def answers_ws(reader: Reader, url: str, entity_type: str) -> str | None:
    """None when the WebSocket opens, welcomes and answers an `x-subscribe` with 201 (API/07)."""
    from websockets.sync.client import connect

    socket = url.replace("https://", "wss://", 1)
    headers = _auth(reader)
    try:
        with connect(socket, subprotocols=["ngsi-ld-ws"], additional_headers=headers, open_timeout=TIMEOUT) as ws:
            welcome = json.loads(ws.recv(timeout=TIMEOUT))
            if welcome.get("metadata", {}).get("x-op") != "x-welcome":
                return f"first message {welcome.get('metadata', {}).get('x-op')!r}"
            ws.send(json.dumps({"metadata": {"x-op": "x-subscribe", "x-id": "s1"},
                                "body": {"type": "Subscription", "entities": [{"type": entity_type}]}}))
            while True:
                message = json.loads(ws.recv(timeout=TIMEOUT))
                meta = message.get("metadata", {})
                if meta.get("x-op") == "x-result" and meta.get("x-id") == "s1":
                    status = meta.get("x-status")
                    if status == 201:
                        ws.send(json.dumps({"metadata": {"x-op": "x-unsubscribe", "x-id": "u1",
                                                         "x-subscription": message["body"]["id"]}}))
                        return None
                    return f"x-subscribe answered {status}"
    except Exception as error:  # noqa: BLE001 - every failure of the handshake is the finding
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status == 401 and not reader.bearer:
            return None
        return f"{type(error).__name__}{f' {status}' if status else ''}"


def answers_mcp(url: str) -> str | None:
    status, body = rpc(url, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                            "clientInfo": {"name": "jc-conformance", "version": "1"}})
    if status == 200 and isinstance(body, dict) and "result" in body:
        return None
    return f"initialize answered {status}"


def first_type(reader: Reader, url: str) -> str | None:
    try:
        answer = reader.http.get(f"{url}/ngsi-ld/v1/types", headers={**_auth(reader), "Accept": "application/json"},
                                 timeout=TIMEOUT)
        types = answer.json().get("typeList") if answer.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None
    return types[0] if types else None


def check_endpoint(reader: Reader, manifest: dict[str, Any], base: str, probe: bool = True) -> Report:
    spec = manifest.get("spec") or {}
    url = endpoint_url(manifest, base)
    name = dataset_name(manifest)
    public = spec.get("audience") == "public"
    report = Report(f"{manifest['metadata'].get('namespace', '')}/{manifest['metadata']['name']}")
    if name is None:
        try:
            held = reader.action("package_show", id=manifest["metadata"]["name"])
        except RuntimeError:
            held = None
        report.add("unpublished", held is None,
                   "no spec.publish.ckan" + ("" if held is None else ", and a dataset exists"))
        return report
    try:
        package = reader.action("package_show", id=name)
    except RuntimeError:
        package = None
    problems = compare(spec, url, package)
    report.add("listed", not problems, "; ".join(problems) or
               f"{len(served(spec))} surfaces: {', '.join(sorted(served(spec)))}")
    if package is None or not probe:
        return report
    kind = first_type(reader, url) or "*"
    for surface in sorted(served(spec)):
        address = url + SURFACES[surface][0]
        if surface == "websocket":
            failure = answers_ws(reader, address, kind)
        elif surface == "mcp" and public:
            failure = answers_mcp(address)
        else:
            failure = answers_http(reader, address, public)
        report.add(f"answers {surface}", failure is None, failure or "ok")
    return report


def lag(
    reader: Reader,
    write_url: str,
    token: str,
    dataset: str,
    entity: str,
    attribute: str,
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> float | None:
    """Seconds from the write's 2xx until the DataStore row holds the value and the dataset's
    `metadata_modified` moved, or None at `timeout` (EP-65, EP-109)."""
    package = reader.action("package_show", id=dataset)
    before = package.get("metadata_modified")
    kind = entity.split(":")[2]
    table = next((r for r in package.get("resources") or [] if r.get("datastore_active") and r.get("name") == kind), None)
    if table is None:
        raise RuntimeError(f"{dataset} has no DataStore table {kind}")
    value = int(datetime.now(UTC).timestamp())
    body = [{"id": entity, "type": kind, attribute: {"type": "Property", "value": value}}]
    answer = requests.post(f"{write_url}/ngsi-ld/v1/entityOperations/upsert", json=body, timeout=TIMEOUT,
                           headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    if answer.status_code >= 300:
        raise RuntimeError(f"the probe write answered {answer.status_code}")
    start = clock()
    while clock() - start < timeout:
        rows = reader.action("datastore_search", resource_id=table["id"], filters=json.dumps({"entity_id": entity}))
        row = (rows.get("records") or [{}])[0]
        moved = reader.action("package_show", id=dataset).get("metadata_modified") != before
        if row.get(f"{attribute}.value") == value and moved:
            return clock() - start
        sleep(10)
    return None


def percentiles(samples: list[float]) -> tuple[float, float]:
    """p50 and p95 of the measured lags (nearest rank)."""
    ordered = sorted(samples)
    rank = max(0, -(-95 * len(ordered) // 100) - 1)
    return statistics.median(ordered), ordered[rank]


def _auth(reader: Reader) -> dict[str, str]:
    return {"Authorization": f"Bearer {reader.bearer}"} if reader.bearer else {}


def endpoints(portal: str, project: str, token: str | None) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    items, after = [], None
    while True:
        answer = requests.get(f"{portal.rstrip('/')}/api/v1/projects/{project}/endpoints", headers=headers,
                              params={"continue": after} if after else None, timeout=TIMEOUT)
        if answer.status_code != 200:
            raise RuntimeError(f"the endpoint list of {project} answered {answer.status_code}")
        page = answer.json()
        items += page.get("items") or []
        after = (page.get("metadata") or {}).get("continue")
        if not after:
            return items


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("projects", nargs="+")
    parser.add_argument("--portal", required=True)
    parser.add_argument("--ckan", required=True)
    parser.add_argument("--base", help="the gateway's base URL when an Endpoint has no status.url")
    parser.add_argument("--write", help="URL of a writable Endpoint in the conformance space, for the lag")
    parser.add_argument("--dataset", help="its CKAN dataset")
    parser.add_argument("--entity", help="the probe entity, urn:ngsi-ld:{Type}:...")
    parser.add_argument("--attribute", help="a numeric attribute the type's table carries")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args(argv)
    reader = Reader(args.ckan, os.environ.get("JC_GATEWAY_TOKEN"))
    base = args.base or args.portal.replace("://portal.", "://", 1)
    ok = True
    for project in args.projects:
        for manifest in endpoints(args.portal, project, os.environ.get("PORTAL_TOKEN")):
            report = check_endpoint(reader, manifest, base)
            ok &= report.ok
            print(report.markdown(), "\n")
    if args.write:
        token = os.environ.get("JC_WRITER_TOKEN")
        if not (token and args.dataset and args.entity and args.attribute):
            print("--write needs JC_WRITER_TOKEN, --dataset, --entity and --attribute", file=sys.stderr)
            return 2
        measured = [lag(reader, args.write.rstrip("/"), token, args.dataset, args.entity, args.attribute, args.timeout)
                    for _ in range(args.samples)]
        done = [s for s in measured if s is not None]
        late = len(measured) - len(done)
        line = (f"DataStore lag of {args.dataset}: p50 {percentiles(done)[0]:.0f} s, p95 {percentiles(done)[1]:.0f} s"
                if done else f"DataStore lag of {args.dataset}: not measured")
        print(f"{line}, {len(done)} of {len(measured)} writes arrived within {args.timeout:.0f} s")
        ok &= late == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

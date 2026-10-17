#!/usr/bin/env python3
"""How much of Banská Bystrica's and BBSK's open data is in NGSI-LD (T-4256).

    python3 tests/assistant/coverage_bb.py \
        --mapping ../docs/Research/bb-bbsk-ngsi-ld-mapping.yaml \
        --seed ../joinedcontext-deployment/components/context-gateway/seed \
        --base https://dev.joinedcontext.com --portal https://portal.dev.joinedcontext.com \
        --out coverage-bb

`PORTAL_TOKEN` (a steward's token, never printed) lets it read the pipeline runs; without it the
last run and its errors stay unknown. One row per `mapped` dataset of the mapping, written to
`<out>.json` and `<out>.md`.

Where each value comes from (the task's "Data sources"):
- dataset, type, space: the T-4254 mapping file (`datasets[]` with `status: mapped`);
- the Endpoint and the pipeline: the deployment seed, the `audience: public` Endpoint whose
  `contextSpaceRef` is the dataset's space (its `slug`), and the pipeline whose Bento mapping
  writes the dataset's URL into `dataset` (its name from the seed's `index.yaml` path);
- entities: `NGSILD-Results-Count` of `GET {base}/api/endpoint/{slug}/ngsi-ld/v1/entities` with
  `type`, `q=isPartOf=="<url>"`, `count=true`, `limit=0`, anonymous;
- last run and error: `items[0]` of `GET {portal}/api/v1/projects/{project}/pipelines/{name}/runs`
  (`lastAt`, `sent`, `rejected`, `failed`).

Exit 1 when a dataset has no entity and no reason why (the T-4256 gate), 0 otherwise; 2 on a
usage error. `--selftest` runs the rows and the gate over canned answers, with no network.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

import yaml

TIMEOUT = 60
WRITES_DATASET = re.compile(r'"dataset":\s*\{\s*"type":\s*"Property",\s*"value":\s*"([^"]+)"')
PIPELINE_PATH = re.compile(r"^projects/([^/]+)/pipelines/([^/]+)/bento\.yaml$")


def mapped(mapping: dict) -> list[dict]:
    """The datasets the mapping turns into entities."""
    return [d for d in mapping.get("datasets") or [] if d.get("status") == "mapped"]


def seed_index(seed: Path) -> tuple[dict, dict]:
    """(project, space) → public Endpoint slug, and dataset URL → (project, pipeline)."""
    doors: dict[tuple[str, str], str] = {}
    pipelines: dict[str, tuple[str, str]] = {}
    for project in sorted(p for p in seed.iterdir() if p.is_dir()):
        index_file = project / "index.yaml"
        index = yaml.safe_load(index_file.read_text()) if index_file.exists() else {}
        for path in sorted(project.glob("*.yaml")):
            text = path.read_text()
            target = PIPELINE_PATH.match(str((index or {}).get(path.name, "")))
            if target:
                for url in WRITES_DATASET.findall(text):
                    pipelines[url] = (target.group(1), target.group(2))
                continue
            for doc in yaml.safe_load_all(text):
                if isinstance(doc, dict) and doc.get("kind") == "Endpoint":
                    spec = doc.get("spec") or {}
                    space = spec.get("contextSpaceRef")
                    space = space.get("name") if isinstance(space, dict) else space
                    key = (doc["metadata"]["namespace"], space)
                    # An App's Endpoint over the same space may narrow it; the space's own door wins.
                    if spec.get("audience") == "public" and spec.get("slug") and (key not in doors or doc["metadata"]["name"] == space):
                        doors[key] = spec["slug"]
    return doors, pipelines


def row_of(dataset: dict, doors: dict, pipelines: dict, get, base: str, portal: str | None) -> dict:
    """One dataset's row: what the broker holds of it and what its pipeline last did."""
    url = dataset["dataset"]
    row = {"dataset": dataset["name"], "url": url, "type": dataset["type"], "entities": None, "lastRun": None, "error": ""}
    slug = doors.get((dataset["org"], dataset["space"]))
    if not slug:
        row["error"] = f"no public Endpoint over space {dataset['space']} in the seed"
        return row
    query = urlencode({"type": dataset["type"], "q": f'isPartOf=="{url}"', "count": "true", "limit": 0})
    status, headers, _ = get(f"{base}/api/endpoint/{slug}/ngsi-ld/v1/entities?{query}", None)
    count = headers.get("NGSILD-Results-Count") if status == 200 else None
    if count is None:
        row["error"] = f"the Endpoint answered {status} for the count"
        return row
    row["entities"] = int(count)
    target = pipelines.get(url)
    if not target:
        if row["entities"] == 0:
            row["error"] = "no pipeline in the seed writes this dataset URL into `dataset` (an older seed may hold its rows without the link)"
        return row
    if not portal:
        return row
    project, name = target
    status, _, body = get(f"{portal}/api/v1/projects/{project}/pipelines/{name}/runs", "portal")
    runs = (body or {}).get("items") if status == 200 else None
    if runs is None:
        row["error"] = row["error"] or f"pipeline {name}: the runs answered {status}"
        return row
    if not runs:
        if row["entities"] == 0:
            row["error"] = f"pipeline {name} has not run yet"
        return row
    last = runs[0]
    row["lastRun"] = last.get("lastAt")
    sent, rejected, failed = (last.get(k, 0) for k in ("sent", "rejected", "failed"))
    if failed or rejected:
        row["error"] = f"pipeline {name}: last run sent {sent}, rejected {rejected}, failed {failed}"
    elif row["entities"] == 0:
        row["error"] = f"pipeline {name}: last run sent {sent} and none is in the broker"
    return row


def unexplained(rows: list[dict]) -> list[str]:
    """The gate: a dataset with no entity and no reason why."""
    return [r["dataset"] for r in rows if not r["entities"] and not r["error"]]


def hidden(mapping: dict) -> list[dict]:
    """Every dataset the mapping does not convert, with its status and reason: data.dev shows none of
    them (T-4317, T-4324)."""
    return [d for d in mapping.get("datasets") or [] if d.get("status") not in ("mapped", "already-ngsi-ld")]


def markdown(rows: list[dict], held_back: list[dict] = ()) -> str:
    lines = ["| dataset | type | entities | last run | error |", "|---|---|---:|---|---|"]
    for r in rows:
        entities = "?" if r["entities"] is None else str(r["entities"])
        lines.append(f"| [{r['dataset']}]({r['url']}) | {r['type']} | {entities} | {r['lastRun'] or ''} | {r['error'].replace('|', '/')} |")
    held = sum(1 for r in rows if r["entities"])
    lines.append("")
    lines.append(f"{held} of {len(rows)} mapped datasets have entities; {sum(r['entities'] or 0 for r in rows)} entities in all.")
    if held_back:
        lines += ["", "| hidden dataset | status | reason |", "|---|---|---|"]
        lines += [f"| {d['name']} | {d['status']} | {str(d.get('reason', '')).replace('|', '/')} |" for d in held_back]
    return "\n".join(lines) + "\n"


def http_get(token: str | None):
    import requests

    def get(url: str, auth: str | None):
        headers = {"Accept": "application/json"}
        if auth == "portal" and token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            answer = requests.get(url, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as error:
            return 0, {}, {"error": type(error).__name__}
        try:
            body = answer.json()
        except ValueError:
            body = None
        return answer.status_code, answer.headers, body

    return get


def selftest() -> None:
    seed = {
        ("bb", "bb-od"): "slug1",
    }
    pipelines = {"https://c/dataset/a": ("bb", "od-a"), "https://c/dataset/b": ("bb", "od-b"), "https://c/dataset/e": ("bb", "od-e")}
    counts = {"https://c/dataset/a": "12", "https://c/dataset/b": "0", "https://c/dataset/c": "0", "https://c/dataset/e": "0"}
    runs = {
        "od-a": [{"lastAt": "2026-10-16T07:00:00Z", "sent": 12, "rejected": 0, "failed": 0}],
        "od-b": [{"lastAt": "2026-10-16T07:00:00Z", "sent": 5, "rejected": 5, "failed": 0}],
        "od-e": [],
    }

    def get(url, auth):
        if "/runs" in url:
            name = url.split("/pipelines/")[1].split("/")[0]
            return 200, {}, {"items": runs[name]}
        for dataset, n in counts.items():
            if urlencode({"q": f'isPartOf=="{dataset}"'}) in url:
                return 200, {"NGSILD-Results-Count": n}, []
        return 404, {}, None

    def ds(name, space="bb-od"):
        return {"name": name, "org": "bb", "space": space, "type": "T", "dataset": f"https://c/dataset/{name}"}

    rows = {r["dataset"]: r for r in (row_of(ds(n), seed, pipelines, get, "https://g", "https://p") for n in "abce")}
    assert rows["a"]["entities"] == 12 and not rows["a"]["error"] and rows["a"]["lastRun"], rows["a"]
    assert rows["b"]["error"] == "pipeline od-b: last run sent 5, rejected 5, failed 0", rows["b"]
    assert rows["c"]["error"].startswith("no pipeline in the seed writes this dataset URL"), rows["c"]
    assert rows["e"]["error"] == "pipeline od-e has not run yet", rows["e"]
    assert row_of(ds("a", "elsewhere"), seed, pipelines, get, "https://g", None)["error"].startswith("no public Endpoint")
    # Without the Portal, a dataset with entities is fine and one without is the gate's red.
    a, b = (row_of(ds(n), seed, pipelines, get, "https://g", None) for n in "ab")
    assert a["entities"] == 12 and not a["error"] and unexplained([a]) == []
    assert unexplained([b]) == ["b"]
    # A refused count is a reason, never a zero.
    refused = row_of(ds("z"), seed, pipelines, lambda u, a: (403, {}, None), "https://g", None)
    assert refused["entities"] is None and refused["error"] == "the Endpoint answered 403 for the count"
    table = markdown(list(rows.values()))
    assert "| [a](https://c/dataset/a) | T | 12 | 2026-10-16T07:00:00Z |  |" in table, table
    assert "1 of 4 mapped datasets have entities; 12 entities in all." in table
    # The seed reader: a public Endpoint's slug by its space, a pipeline by the dataset it writes.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "bb"
        project.mkdir()
        (project / "index.yaml").write_text("p.yaml: projects/bb/pipelines/od-a/bento.yaml\ne.yaml: x\n")
        (project / "p.yaml").write_text('root = { "dataset": { "type": "Property", "value": "https://c/dataset/a" } }\n')
        (project / "e.yaml").write_text(
            "kind: Endpoint\nmetadata: {name: app, namespace: bb}\nspec: {contextSpaceRef: {kind: ContextSpace, name: bb-od}, slug: s0, audience: public}\n---\n"
            "kind: Endpoint\nmetadata: {name: bb-od, namespace: bb}\nspec: {contextSpaceRef: bb-od, slug: s1, audience: public}\n---\n"
            "kind: Endpoint\nmetadata: {name: f, namespace: bb}\nspec: {contextSpaceRef: bb-in, slug: s2, audience: organization}\n"
        )
        doors, found = seed_index(Path(tmp))
        assert doors == {("bb", "bb-od"): "s1"}, doors
        assert found == {"https://c/dataset/a": ("bb", "od-a")}, found
    assert [d["name"] for d in mapped({"datasets": [{"name": "x", "status": "mapped"}, {"name": "y", "status": "not-ingestible"}]})] == ["x"]
    some = {"datasets": [{"name": "x", "status": "mapped"}, {"name": "n", "status": "already-ngsi-ld"},
                         {"name": "p", "status": "no-data-source", "reason": "a PDF map sheet"}]}
    assert [d["name"] for d in hidden(some)] == ["p"]
    assert "| p | no-data-source | a PDF map sheet |" in markdown([], hidden(some))
    print("coverage_bb selftest ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--base", default=os.environ.get("JC_DEV_BASE", "https://dev.joinedcontext.com"))
    parser.add_argument("--portal", default=os.environ.get("JC_DEV_PORTAL"))
    parser.add_argument("--out", type=Path, default=Path("coverage-bb"))
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.mapping or not args.seed:
        parser.error("--mapping and --seed are required")
    token = os.environ.get("PORTAL_TOKEN")
    portal = args.portal if token else None
    if not portal:
        print("no PORTAL_TOKEN: the last runs stay unknown", file=sys.stderr)
    doors, pipelines = seed_index(args.seed)
    get = http_get(token)
    mapping = yaml.safe_load(args.mapping.read_text())
    rows = [row_of(d, doors, pipelines, get, args.base.rstrip("/"), portal and portal.rstrip("/")) for d in mapped(mapping)]
    args.out.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    table = markdown(rows, hidden(mapping))
    args.out.with_suffix(".md").write_text(table)
    print(table)
    missing = unexplained(rows)
    if missing:
        print(f"no entity and no reason: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

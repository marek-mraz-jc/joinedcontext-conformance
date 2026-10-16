#!/usr/bin/env python3
"""data.dev's publish gate: a dataset stays listed only when it is in NGSI-LD, matches its
source, can be asked about, and names a Smart Data Model (T-4318, T-4319).

    python3 tests/assistant/publish_gate.py \
        --mapping ../docs/Research/bb-bbsk-ngsi-ld-mapping.yaml --out evidence/publish-gate

Reads only public data: the catalogue, the hub's endpoint list, the broker's public Endpoints,
the sources the mapping names, and an eval copy of the assistant (a publicId with an `eval`
segment, never the visitors' deployment). Writes `<out>.json`, `<out>.md` and `<out>.hidden.txt`
(one package name per line: what the catalogue's hide switch reads).

Checks per catalogue package (the task's 1 to 4):
1. NGSI-LD: the dataset has entities on dev. Which distributions the package lists (NGSI-LD,
   MCP, CSV, no ZIP, links answering 200) is T-4324's catalogue test, not this gate's.
2. Accurate: entity count within 1 % of the source's rows; 5 rows spread over the source match
   their entities attribute by attribute (locations within 10 m); every sampled location inside
   the publisher's box; no source key twice among the sampled entities; a daily dataset's newest
   observedAt no older than a day.
3. Queryable: the eval assistant answers "how many" with the entity count and cites an Endpoint.
4. Smart Data Model: the mapping names one (validation itself is T-4319's).

Verdict: `hide` when a check fails, `keep` when all four pass, `hold` when none fails but one
could not run (no mapping, no eval copy for the publisher): a hold stays as it is and is listed.

Where each value comes from (the task's "Data sources"):
- packages and distributions: `package_search` of the catalogue, `resources[].format == NGSI-LD`;
- type, space, source, keys, properties: the T-4254 mapping (`datasets[]`);
- the Endpoint of a space: the hub's `list_endpoints` (`space`, `types`, `dcat`);
- entity counts: `NGSILD-Results-Count` of `GET {endpoint}ngsi-ld/v1/entities?type&q=dataset==…`;
- source rows: Esri `query?returnCountOnly`, CKAN `datastore_search` `total`, WordPress
  `X-WP-Total`; sampled rows by offset from the same services;
- the answer: the eval copy's SSE `answer` and `citations` events.

Exit 0 when the run completed (whatever the verdicts), 2 on a usage error.
`--selftest` runs every check over canned answers, with no network.
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

TIMEOUT = 60
CKAN = "https://data.dev.joinedcontext.com"
HUB = "https://dev.joinedcontext.com/api/mcp"
GATEWAY = "https://dev.joinedcontext.com"
EVAL_ID = "bb-eval-gemini"
SAMPLE = 100
# Publisher -> (west, south, east, north), a little wider than the body's territory.
BOXES = {
    "bbsk": (18.55, 47.90, 20.55, 49.30),
    "banskabystrica": (18.95, 48.60, 19.35, 48.90),
    "hel-fi": (24.50, 59.90, 25.50, 60.45),
    "zilina": (18.55, 49.10, 18.95, 49.35),
    "praha": (14.20, 49.90, 14.75, 50.20),
}
# The generator's publisher domains (joinedcontext-deployment scripts/open-data/generate.py).
DOMAINS = {"banskabystrica": "banskabystrica.sk", "bbsk": "bbsk.sk"}
TAKEN = {"dataProvider", "source", "dataset", "location", "observedAt", "name"}
PERIODS = {"P1D": dt.timedelta(days=1), "P1W": dt.timedelta(weeks=1), "P1M": dt.timedelta(days=31)}


# --- what the generator writes (kept in step with scripts/open-data/generate.py) ---------------

def slugs(entries):
    out = {}
    for e in entries:
        base = re.sub(r"^(bbsk|bb)-", "", e["name"])
        base = re.sub(r"-[0-9a-f]{6}$", "", base)[:40].rstrip("-")
        base = re.sub(r"(-[0-9]+)+$", "", base)
        name, n = base, 2
        while name in out.values():
            name = f"{base[:37].rstrip('-')}-{n}"
            n += 1
        out[e["name"]] = name
    return out


def attribute(name):
    return "column" + name[0].upper() + name[1:] if name in TAKEN else name


def entity_id(e, slug, row):
    """The id a keyed row gets, or None for a row-hash dataset or a row without its key."""
    keys = e["entityId"].get("keys")
    if not keys or any(row.get(k) in (None, "") for k in keys):
        return None
    local = "-".join(text(row[k]) for k in keys)
    local = re.sub(r"[^A-Za-z0-9._~-]", "-", local)[:80]
    return f"urn:ngsi-ld:{e['type']}:{DOMAINS[e['org']]}:{e['space']}:{slug}-{local}"


def text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def expected(rng, raw):
    """The value the generator stores for a source cell, or None when it stores none."""
    if raw is None:
        return None
    if rng in ("integer", "float"):
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        return round(number) if rng == "integer" else number
    if rng in ("datetime", "date"):
        if isinstance(raw, (int, float)):
            moment = dt.datetime.fromtimestamp(raw / 1000, dt.timezone.utc)
        else:
            moment = parse_time(raw)
            if moment is None:
                return None
        return moment.date().isoformat() if rng == "date" else moment
    value = text(raw)
    return value or None


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)


def value_of(entity, name):
    attr = entity.get(name)
    if isinstance(attr, list):
        attr = attr[0] if attr else None
    if isinstance(attr, dict):
        return attr.get("value", attr.get("object"))
    return attr


# --- the checks: pure functions over what was read --------------------------------------------

def count_ok(rows, entities):
    if rows is None or entities is None:
        return None
    if abs(entities - rows) <= max(1, rows) * 0.01:
        return ""
    return f"{entities} entities for {rows} source rows ({(entities - rows) / max(1, rows):+.1%})"


def row_mismatch(e, row, geometry, entity):
    """The attributes where an entity differs from its source row."""
    wrong = []
    for name, prop in e["properties"].items():
        if e["source"]["kind"] == "json-feed" and prop["column"] == "content":
            continue  # stored with its HTML stripped by Bento
        raw = row.get(prop["column"])
        if isinstance(raw, dict):
            raw = raw.get("rendered")
        want, have = expected(prop["range"], raw), value_of(entity, attribute(name))
        if want is None:
            continue
        if isinstance(want, dt.datetime):
            ok = parse_time(have) == want.replace(microsecond=0)
        elif isinstance(want, float):
            ok = isinstance(have, (int, float)) and math.isclose(want, have, rel_tol=1e-9, abs_tol=1e-9)
        else:
            ok = text(have) == text(want) if have is not None else False
        if not ok:
            wrong.append(f"{attribute(name)}: source {want!s:.40}, broker {have!s:.40}")
    if geometry and geometry.get("coordinates") is not None:
        have = value_of(entity, "location")
        a, b = first_point(geometry), first_point(have or {})
        if b is None or metres(a, b) > 10:
            wrong.append(f"location: source {a}, broker {b}")
    return wrong


def first_point(geometry):
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    return tuple(coords[:2]) if isinstance(coords, list) and len(coords) >= 2 else None


def points(geometry):
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    stack, out = [coords], []
    while stack:
        c = stack.pop()
        if isinstance(c, list) and c and all(isinstance(x, (int, float)) for x in c[:2]) and len(c) >= 2:
            out.append((c[0], c[1]))
        elif isinstance(c, list):
            stack.extend(c)
    return out


def metres(a, b):
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((a[0] - b[0]) * 111_320 * math.cos(lat), (a[1] - b[1]) * 110_540)


def outside(org, entities):
    box = BOXES.get(org)
    if not box:
        return ""
    for entity in entities:
        for lon, lat in points(value_of(entity, "location") or {}):
            if not (box[0] <= lon <= box[2] and box[1] <= lat <= box[3]):
                return f"{entity.get('id')} at ({lon}, {lat}) is outside {org}'s box"
    return ""


def duplicates(e, entities):
    keys = e["entityId"].get("keys")
    if keys:
        by_column = {p["column"]: attribute(n) for n, p in e["properties"].items()}
        names = [by_column.get(k) for k in keys]
        if None in names:
            return ""
    else:
        names = sorted(attribute(n) for n in e["properties"])
    seen = {}
    for entity in entities:
        key = tuple(json.dumps(value_of(entity, n), sort_keys=True) for n in names)
        if key in seen:
            return f"{seen[key]} and {entity.get('id')} carry the same {', '.join(names)}"
        seen[key] = entity.get("id")
    return ""


def stale(e, entities, now):
    if e.get("refresh") != "P1D" or not e.get("observedAt"):
        return ""
    times = [parse_time(value_of(x, "observedAt") or (x.get("observedAt") if isinstance(x.get("observedAt"), str) else None)) for x in entities]
    times = [t for t in times if t]
    if not times:
        return "no sampled entity carries observedAt"
    newest = max(times)
    return "" if now - newest <= PERIODS["P1D"] else f"newest observedAt {newest.isoformat()} is older than a day"


def answered(answer, citations, want):
    if want is None:
        return "no count to ask about"
    digits = re.sub(r"(?<=\d)[\s .,](?=\d{3}\b)", "", re.sub(r"\[\d+(?:,\s*\d+)*\]", " ", answer))
    if str(want) not in re.findall(r"\d+", digits):
        return f"the answer does not state {want}: {answer[:120]!r}"
    if not any(c.get("endpoint") for c in citations):
        return "the answer cites no Endpoint"
    return ""


def verdict(checks):
    states = [c["state"] for c in checks.values()]
    return "hide" if "fail" in states else "hold" if "not run" in states else "keep"


def check(state, reason=""):
    return {"state": state, "reason": reason}


# --- reading -----------------------------------------------------------------------------------

class Http:
    def get(self, url, accept="application/json"):
        request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "joinedcontext-publish-gate/1"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
                status, headers = response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, status, headers = error.read(), error.code, dict(error.headers or {})
        except (urllib.error.URLError, OSError) as error:
            return 0, {}, {"error": str(error)}
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        return status, {k.lower(): v for k, v in headers.items()}, body


def endpoints(http):
    """space -> [(dcat url, types)] of the public Endpoints the hub lists."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_endpoints", "arguments": {}}}
    request = urllib.request.Request(HUB, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read().decode()
    if raw.lstrip().startswith(("event:", "data:")):
        raw = next(line[5:] for line in raw.splitlines() if line.startswith("data:"))
    result = json.loads(raw)["result"]
    content = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    out = {}
    for e in content.get("endpoints", content) if isinstance(content, dict) else content:
        out.setdefault(e["space"], []).append((e["dcat"], e.get("types", [])))
    return out


def entities_url(base, **params):
    return f"{base}ngsi-ld/v1/entities?{urllib.parse.urlencode(params)}"


def count(http, base, kind, q=None):
    params = {"type": kind, "count": "true", "limit": 0}
    if q:
        params["q"] = q
    status, headers, body = http.get(entities_url(base, **params))
    n = headers.get("ngsild-results-count")
    return (int(n), "") if status == 200 and n is not None else (None, f"the Endpoint answered {status} {(body or {}).get('title', '') if isinstance(body, dict) else ''}".strip())


def source_count(http, e):
    s = e["source"]
    if s["kind"] == "esri-featureserver":
        where = (e.get("rowFilter") or {}).get("where", "1=1")
        status, _, body = http.get(f"{s['url']}/query?{urllib.parse.urlencode({'where': where, 'returnCountOnly': 'true', 'f': 'json'})}")
        return (body or {}).get("count") if status == 200 else None
    if s["kind"] == "ckan-datastore":
        status, _, body = http.get(f"{CKAN}/api/3/action/datastore_search?resource_id={s['resource']}&limit=0")
        return ((body or {}).get("result") or {}).get("total") if status == 200 else None
    status, headers, _ = http.get(f"{s['url']}?per_page=1")
    total = headers.get("x-wp-total")
    return int(total) if status == 200 and total and total.isdigit() else None


def source_row(http, e, offset):
    """(row, geometry) at an offset of the source, or (None, None)."""
    s = e["source"]
    if s["kind"] == "esri-featureserver":
        params = {"where": (e.get("rowFilter") or {}).get("where", "1=1"), "outFields": "*", "resultOffset": offset,
                  "resultRecordCount": 1, "returnGeometry": "true" if s.get("geometry") else "false",
                  "outSR": "4326", "geometryPrecision": "6", "f": "geojson"}
        status, _, body = http.get(f"{s['url']}/query?{urllib.parse.urlencode(params)}")
        features = (body or {}).get("features") if status == 200 else None
        return (features[0].get("properties") or {}, features[0].get("geometry")) if features else (None, None)
    if s["kind"] == "ckan-datastore":
        status, _, body = http.get(f"{CKAN}/api/3/action/datastore_search?resource_id={s['resource']}&limit=1&offset={offset}")
        records = ((body or {}).get("result") or {}).get("records") if status == 200 else None
        return (records[0], None) if records else (None, None)
    status, _, body = http.get(f"{s['url']}?per_page=1&page={offset + 1}")
    return (body[0], None) if status == 200 and isinstance(body, list) and body else (None, None)


def find_entity(http, base, e, slug, row, dataset_q):
    ident = entity_id(e, slug, row)
    if ident:
        status, _, body = http.get(f"{base}ngsi-ld/v1/entities/{urllib.parse.quote(ident, safe='')}")
        return body if status == 200 and isinstance(body, dict) else None
    # A row-hash id is the hash of every mapped cell, so the row's entity is the one that carries
    # all of them: match on every string and whole-number cell the q grammar can quote.
    terms = [dataset_q]
    for name, prop in e["properties"].items():
        value = expected(prop["range"], row.get(prop["column"])) if prop["range"] in ("string", "integer") else None
        if isinstance(value, int) and not isinstance(value, bool):
            terms.append(f"{attribute(name)}=={value}")
        elif isinstance(value, str) and not re.search(r'["\\;|()]', value) and len(value) <= 200:
            terms.append(f'{attribute(name)}=="{value}"')
    if len(terms) == 1:
        return None
    status, _, body = http.get(entities_url(base, type=e["type"], q=";".join(terms), limit=1))
    return body[0] if status == 200 and isinstance(body, list) and body else None


# --- one package -------------------------------------------------------------------------------

def gate(package, e, slug, spaces, http, ask, now, shared):
    org = (package.get("organization") or {}).get("name")
    ngsi = [r["url"] for r in package.get("resources", []) if (r.get("format") or "").upper() == "NGSI-LD"]
    checks = {}
    row = {"package": package["name"], "org": org, "type": None, "entities": None, "rows": None}

    if e is None or e["status"] != "mapped":
        if e is not None and e["status"] == "not-ingestible":
            checks["ngsi-ld"] = check("fail", f"not ingestible (T-4324 deletes it): {e.get('reason', '')}")
        elif ngsi:
            door = ngsi[0].split("ngsi-ld/v1")[0]
            types = next((t for doors in spaces.values() for d, t in doors if d == door), [])
            counts = [count(http, door, kind) for kind in types]
            row["entities"] = sum(n for n, _ in counts if n) if counts else None
            why = next((w for n, w in counts if n is None), "") or f"the hub lists no type for {door}"
            checks["ngsi-ld"] = check("pass") if row["entities"] else check("fail", f"no entity behind the NGSI-LD distribution ({why})")
        else:
            checks["ngsi-ld"] = check("fail", "no NGSI-LD distribution and no mapping to one")
        why = "the platform publishes it from its own Endpoint; no mapping names an outside source" if ngsi and e is None or (e or {}).get("status") == "already-ngsi-ld" else "no mapping"
        checks["accurate"] = check("not run", why)
        checks["queryable"] = check("not run", "no mapped type to ask about")
        checks["smart-data-model"] = check("not run", "no mapping")
        return row | {"checks": checks, "verdict": verdict(checks)}

    row["type"] = e["type"]
    doors = [d for d, types in spaces.get(e["space"], []) if e["type"] in types]
    dataset_q = f'dataset=="{e["dataset"]}"'
    if not doors:
        checks["ngsi-ld"] = check("fail", f"no public Endpoint over {e['space']} lists {e['type']}")
    else:
        base = doors[0]
        n, why = count(http, base, e["type"], dataset_q)
        if n is None and shared.get((e["space"], e["type"]), 0) == 1:
            n, why = count(http, base, e["type"])
        row["entities"] = n
        if n is None:
            checks["ngsi-ld"] = check("fail", why)
        elif n == 0:
            checks["ngsi-ld"] = check("fail", "no entity on dev")
        else:
            checks["ngsi-ld"] = check("pass")

    if not row["entities"]:
        checks["accurate"] = check("not run", "no entity to compare")
    else:
        problems = []
        row["rows"] = source_count(http, e)
        c = count_ok(row["rows"], row["entities"])
        if c is None:
            problems.append("the source did not answer its row count")
        elif c:
            problems.append(c)
        status, _, sample = http.get(entities_url(base, type=e["type"], q=dataset_q, limit=SAMPLE))
        if status != 200 or not isinstance(sample, list):
            status, _, sample = http.get(entities_url(base, type=e["type"], limit=SAMPLE))
        sample = sample if isinstance(sample, list) else []
        problems += [p for p in (outside(org, sample), duplicates(e, sample), stale(e, sample, now)) if p]
        total = row["rows"] or 0
        for offset in sorted({0, total // 4, total // 2, 3 * total // 4, max(0, total - 1)}):
            source, geometry = source_row(http, e, offset)
            if source is None:
                problems.append(f"source row {offset} could not be read")
                continue
            entity = find_entity(http, base, e, slug, source, dataset_q)
            if entity is None:
                problems.append(f"source row {offset} has no entity")
                continue
            wrong = row_mismatch(e, source, geometry if e["source"].get("geometry") else None, entity)
            if wrong:
                problems.append(f"row {offset} ({entity.get('id')}): " + "; ".join(wrong[:3]))
        checks["accurate"] = check("fail", " | ".join(problems)) if problems else check("pass")

    model = e.get("smartDataModel")
    sdm = check("pass") if model else check("fail", "the mapping names no Smart Data Model (T-4319)")
    if "fail" in (checks["ngsi-ld"]["state"], checks["accurate"]["state"], sdm["state"]):
        checks["queryable"] = check("not run", "asked only when checks 1, 2 and 4 pass (the eval copy's hourly limit)")
    elif org not in DOMAINS or ask is None:
        checks["queryable"] = check("not run", f"no eval copy of {org}'s assistant")
    else:
        question = f"Koľko záznamov typu {e['type']} je v datasete „{package['title']}“? Odpovedz jedným číslom."
        answer, citations = ask(question)
        why = answered(answer, citations, row["entities"])
        checks["queryable"] = check("fail", why) if why else check("pass")
    checks["smart-data-model"] = sdm

    return row | {"checks": checks, "verdict": verdict(checks)}


# --- the run -----------------------------------------------------------------------------------

def run(packages, mapping, spaces, http, ask, now):
    by_name = {e["name"]: e for e in mapping}
    names = slugs([e for e in mapping if e["status"] == "mapped"])
    shared = {}
    for e in mapping:
        if e["status"] == "mapped":
            shared[(e["space"], e["type"])] = shared.get((e["space"], e["type"]), 0) + 1
    return [gate(p, by_name.get(p["name"]), names.get(p["name"]), spaces, http, ask, now, shared) for p in packages]


def markdown(rows, when):
    lines = [f"Publish gate over data.dev, {when} (T-4318).", "",
             "| package | org | type | entities | source rows | NGSI-LD | accurate | queryable | SDM | verdict |",
             "|---|---|---|---:|---:|---|---|---|---|---|"]
    for r in rows:
        cells = [f"{c['state']}{': ' + c['reason'] if c['reason'] else ''}".replace("|", "/")[:220] for c in r["checks"].values()]
        lines.append(f"| {r['package']} | {r['org']} | {r['type'] or ''} | {'' if r['entities'] is None else r['entities']} | "
                     f"{'' if r['rows'] is None else r['rows']} | " + " | ".join(cells) + f" | **{r['verdict']}** |")
    tally = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("keep", "hold", "hide")}
    lines += ["", f"{len(rows)} packages: keep {tally['keep']}, hold {tally['hold']}, hide {tally['hide']}."]
    return "\n".join(lines) + "\n"


def eval_asker(base, public_id):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import eval_bb

    if not eval_bb.eval_deployment(public_id):
        sys.exit(f"{public_id} is not an eval copy (T-4286): refused")
    last = [0.0]

    def ask(question):
        time.sleep(max(0.0, last[0] + 61 - time.monotonic()))  # 60 per client per hour
        last[0] = time.monotonic()
        try:
            events, _, _ = eval_bb.ask(base, public_id, "https://data.dev.joinedcontext.com", question)
        except (urllib.error.URLError, OSError) as error:
            return f"(no answer: {error})", []
        citations = next((d for n, d, *_ in events if n == "citations"), []) or []
        return eval_bb.answer_text(events), citations

    return ask


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--catalogue", default=CKAN)
    parser.add_argument("--eval", default=EVAL_ID, help="the eval copy's publicId; 'none' skips check 3")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.mapping or not args.out:
        parser.print_usage()
        return 2
    http = Http()
    status, _, body = http.get(f"{args.catalogue}/api/3/action/package_search?rows=1000")
    if status != 200:
        sys.exit(f"the catalogue answered {status}")
    packages = sorted(body["result"]["results"], key=lambda p: p["name"])
    mapping = yaml.safe_load(args.mapping.read_text())["datasets"]
    ask = None if args.eval == "none" else eval_asker(GATEWAY, args.eval)
    now = dt.datetime.now(dt.timezone.utc)
    rows = run(packages, mapping, endpoints(http), http, ask, now)
    when = now.strftime("%Y-%m-%dT%H:%MZ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{args.out}.json").write_text(json.dumps({"at": when, "rows": rows}, indent=1, ensure_ascii=False) + "\n")
    Path(f"{args.out}.md").write_text(markdown(rows, when))
    Path(f"{args.out}.hidden.txt").write_text("".join(f"{r['package']}\n" for r in rows if r["verdict"] == "hide"))
    print(markdown(rows, when).splitlines()[-1])
    return 0


# --- selftest ----------------------------------------------------------------------------------

def selftest():
    now = dt.datetime(2026, 10, 16, 12, tzinfo=dt.timezone.utc)
    base = "https://gw/api/endpoint/abc/"
    e = {"name": "bb-skoly", "org": "banskabystrica", "status": "mapped", "type": "School", "space": "bb-od",
         "dataset": "https://cat/dataset/bb-skoly", "refresh": "P1D", "observedAt": "updated", "smartDataModel": "School",
         "entityId": {"keys": ["ID"]},
         "source": {"kind": "ckan-datastore", "resource": "r1", "url": "x", "rows": 3},
         "properties": {"sourceId": {"column": "ID", "range": "integer"}, "nazov": {"column": "Nazov", "range": "string"},
                        "ziaci": {"column": "Ziaci", "range": "integer"}}}
    slug = slugs([e])["bb-skoly"]
    source = [{"ID": 1, "Nazov": "ZŠ Moskovská", "Ziaci": 410}, {"ID": 2, "Nazov": "ZŠ Golianova", "Ziaci": 300},
              {"ID": 3, "Nazov": "Gymnázium", "Ziaci": 520}]

    def entity_of(r, **over):
        x = {"id": entity_id(e, slug, r), "type": "School", "sourceId": {"type": "Property", "value": r["ID"]},
             "nazov": {"type": "Property", "value": r["Nazov"]}, "ziaci": {"type": "Property", "value": r["Ziaci"]},
             "observedAt": {"type": "Property", "value": "2026-10-16T08:00:00Z"},
             "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [19.15, 48.73]}}}
        x.update(over)
        return x

    def world(rows=source, entities=None, count=None):
        entities = entities if entities is not None else [entity_of(r) for r in rows]
        by_id = {x["id"]: x for x in entities}

        class Fake:
            def get(self, url, accept=None):
                if "datastore_search" in url:
                    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                    if qs["limit"] == ["0"]:
                        return 200, {}, {"result": {"total": len(rows)}}
                    k = int(qs["offset"][0])
                    return 200, {}, {"result": {"records": rows[k:k + 1]}}
                if "/entities/" in url:
                    x = by_id.get(urllib.parse.unquote(url.rsplit("/", 1)[1]))
                    return (200, {}, x) if x else (404, {}, {"title": "ResourceNotFound"})
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                if qs.get("count") == ["true"]:
                    return 200, {"ngsild-results-count": str(len(entities) if count is None else count)}, []
                return 200, {}, entities
        return Fake()

    package = {"name": "bb-skoly", "title": "Školy", "organization": {"name": "banskabystrica"},
               "resources": [{"format": "NGSI-LD", "url": base + "ngsi-ld/v1/"}]}
    spaces = {"bb-od": [(base, ["School"])]}
    right = lambda q: ("Je ich 3 [1].", [{"endpoint": "bb-od"}])

    def verdict_of(http, ask=right, pkg=package, entry=e):
        return gate(pkg, entry, slug, spaces, http, ask, now, {("bb-od", "School"): 1})

    clean = verdict_of(world())
    assert clean["verdict"] == "keep", clean
    cases = {
        "count mismatch": verdict_of(world(count=5)),
        "a wrong value": verdict_of(world(entities=[entity_of(source[0]), entity_of(source[1], ziaci={"type": "Property", "value": 299}), entity_of(source[2])])),
        "outside the box": verdict_of(world(entities=[entity_of(source[0]), entity_of(source[1]), entity_of(source[2], location={"type": "GeoProperty", "value": {"type": "Point", "coordinates": [24.9, 60.2]}})])),
        "a duplicate id": verdict_of(world(entities=[entity_of(source[0]), entity_of(source[1]), entity_of(source[2]), dict(entity_of(source[2]), id="urn:other")], count=3)),
        "stale observedAt": verdict_of(world(entities=[entity_of(r, observedAt={"type": "Property", "value": "2026-10-10T08:00:00Z"}) for r in source])),
        "the wrong number": verdict_of(world(), ask=lambda q: ("Je ich 4 [1].", [{"endpoint": "bb-od"}])),
        "no Smart Data Model": verdict_of(world(), entry=dict(e, smartDataModel=None)),
    }
    for name, result in cases.items():
        assert result["verdict"] == "hide", (name, result)
    assert "5 entities for 3 source rows" in cases["count mismatch"]["checks"]["accurate"]["reason"]
    assert "ziaci" in cases["a wrong value"]["checks"]["accurate"]["reason"], cases["a wrong value"]
    assert "outside" in cases["outside the box"]["checks"]["accurate"]["reason"]
    assert "same" in cases["a duplicate id"]["checks"]["accurate"]["reason"]
    assert "older than a day" in cases["stale observedAt"]["checks"]["accurate"]["reason"]
    assert "does not state 3" in cases["the wrong number"]["checks"]["queryable"]["reason"]
    # Distributions are T-4324's: a package without an NGSI-LD link still passes check 1 here.
    assert verdict_of(world(), pkg=dict(package, resources=[{"format": "CSV", "url": "x"}]))["verdict"] == "keep"
    # No eval copy for the publisher: held, not hidden.
    held = verdict_of(world(), ask=None)
    assert held["verdict"] == "hold" and held["checks"]["queryable"]["state"] == "not run", held
    # A ZIP of map sheets is hidden; a platform Endpoint without a mapping is held.
    zip_only = gate({"name": "bb-dtm", "organization": {"name": "banskabystrica"}, "resources": [{"format": "ZIP"}]},
                    {"name": "bb-dtm", "status": "not-ingestible", "reason": "a PDF map sheet"}, None, spaces, world(), right, now, {})
    assert zip_only["verdict"] == "hide" and "PDF" in zip_only["checks"]["ngsi-ld"]["reason"]
    own = gate(package | {"name": "helsinki-bikes"}, None, None, spaces, world(), right, now, {})
    assert own["verdict"] == "hold", own
    # Numbers the assistant writes with a thousands separator still count.
    assert answered("V meste je 16 304 adries [2].", [{"endpoint": "x"}], 16304) == ""
    assert answered("Je ich 12.", [], 12) == "the answer cites no Endpoint"
    # A date-time from Esri's epoch milliseconds compares as an instant.
    assert expected("datetime", 1_760_000_000_000) == dt.datetime(2025, 10, 9, 8, 53, 20, tzinfo=dt.timezone.utc)
    assert entity_id(e, slug, {"ID": 7.0}) == f"urn:ngsi-ld:School:banskabystrica.sk:bb-od:{slug}-7"
    print("publish_gate selftest: ok")


if __name__ == "__main__":
    sys.exit(main())

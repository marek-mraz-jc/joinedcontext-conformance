#!/usr/bin/env python3
"""data.dev's publish gate: a dataset stays listed only when it is in NGSI-LD, matches its
source, can be asked about, and names a Smart Data Model (T-4318, T-4319).

    python3 tests/assistant/publish_gate.py \
        --mapping ../docs/Research/bb-bbsk-ngsi-ld-mapping.yaml --out evidence/publish-gate \
        --distributions ../joinedcontext-deployment/charts/ckan/files/od-datasets.json \
        --sdm ../joinedcontext-deployment/scripts/open-data/sdm

Reads only public data: the catalogue, the hub's endpoint list, the broker's public Endpoints,
the sources the mapping names, and an eval copy of the assistant (a publicId with an `eval`
segment, never the visitors' deployment). Writes `<out>.json`, `<out>.md` and `<out>.hidden.txt`
(one package name per line: what the catalogue's hide switch reads).

The catalogue leaves the packages it hides out of the public search, so a rerun names them with
`--also <file>` (one name per line, e.g. the last `<out>.hidden.txt`); each is gated from its
mapping entry, whose org and title stand in for the package's (T-4448).

Checks per catalogue package (the task's 1 to 4):
1. NGSI-LD: the dataset has entities on dev. Which distributions the package lists (NGSI-LD,
   MCP, CSV, no ZIP, links answering 200) is T-4324's catalogue test, not this gate's.
2. Accurate: entity count within 1 % of the source's rows; 5 rows spread over the source match
   their entities attribute by attribute (locations within 10 m); every sampled location inside
   the publisher's box; no source key twice among the sampled entities; a daily dataset's newest
   observedAt no older than a day.
3. Queryable: the eval assistant answers "how many" with the entity count and cites an Endpoint.
4. Smart Data Model: the mapping names one, and with `--sdm` (the deployment's vendored
   schemas, T-4319) every sampled entity, as keyValues, validates against its schema.json.

Verdict: `hide` when a check fails, `keep` when all four pass, `hold` when none fails but one
could not run (no mapping, no eval copy for the publisher): a hold stays as it is and is listed.

Where each value comes from (the task's "Data sources"):
- packages and distributions: `package_search` of the catalogue, `resources[].format == NGSI-LD`;
- type, space, source, keys, properties: the T-4254 mapping (`datasets[]`);
- the Endpoint of a space: the hub's `list_endpoints` (`space`, `types`, `dcat`);
- entity counts: `NGSILD-Results-Count` of `GET {endpoint}ngsi-ld/v1/entities?type&q=isPartOf==…`;
- source rows: Esri `query?returnCountOnly`, CKAN `datastore_search` `total`, WordPress
  `X-WP-Total`; for an entry with a `rowFilter.keep` or a row-hash id, every row read from the same
  services, counting the rows the keep pattern passes and, for a row hash, their distinct mapped
  cells, as the pipeline does (T-4517); sampled rows by offset from the same services;
- the answer: the eval copy's SSE `answer` and `citations` events.

Exit 0 when the run completed (whatever the verdicts), 2 on a usage error.
`--selftest` runs every check over canned answers, with no network.
"""

import argparse
import datetime as dt
import io
import json
import math
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import yaml

TIMEOUT = 60
PAGE, PART_ROWS = 1000, 4000  # the generator's rows per Esri page and per esri-layers pipeline
CKAN = "https://data.dev.joinedcontext.com"
HUB = "https://api.dev.joinedcontext.com/api/mcp"
GATEWAY = "https://api.dev.joinedcontext.com"
EVAL_ID = "bb-eval-gemini"
SAMPLE = 100
# Publisher -> (west, south, east, north), a little wider than the body's territory.
BOXES = {
    # Banskobystrický kraj (NUTS3 SK032) in BBSK's own Kraj3_WebMercator layer, the source of
    # bbsk-kraj-bbk-hranica: 18.478678, 48.053834 to 20.4699, 48.947247, widened by 0.02° (T-4518).
    # The typed box started at 18.55 E and cut off the Žarnovica district.
    "bbsk": (18.45, 48.03, 20.49, 48.97),
    "banskabystrica": (18.95, 48.60, 19.35, 48.90),
    "hel-fi": (24.50, 59.90, 25.50, 60.45),
    "zilina": (18.55, 49.10, 18.95, 49.35),
    "praha": (14.20, 49.90, 14.75, 50.20),
}
# The generator's publisher domains (joinedcontext-deployment scripts/open-data/generate.py).
DOMAINS = {"banskabystrica": "banskabystrica.sk", "bbsk": "bbsk.sk"}
# The attributes the generator sets itself, so a column of that name is renamed (generate.py TAKEN).
TAKEN = {"dataProvider", "source", "isPartOf", "location", "observedAt"}
PERIODS = {"P1D": dt.timedelta(days=1), "P1W": dt.timedelta(weeks=1), "P1M": dt.timedelta(days=31)}


COMMON = ("https://smart-data-models.github.io/data-models/common-schema.json",
          "https://raw.githubusercontent.com/smart-data-models/data-models/master/common-schema.json")


def sdm_validators(folder):
    """model -> a function from an entity to its schema errors, over the vendored schemas in
    `folder` (`<Subject>/<Model>/schema.json` and `common-schema.json`); never the network."""
    import jsonschema
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT7

    common = Resource.from_contents(json.loads((folder / "common-schema.json").read_text()), DRAFT7)
    registry = Registry().with_resources((u, common) for u in COMMON)
    cache = {}

    def errors(model, entity):
        if model not in cache:
            # An `extension/<Type>` is the generator's own schema (its base inlined), in `extensions/`.
            path = folder / (f"extensions/{model.removeprefix('extension/')}" if model.startswith("extension/")
                             else model.removeprefix("dataModel.")) / "schema.json"
            cache[model] = jsonschema.Draft7Validator(
                json.loads(path.read_text()), registry=registry,
                format_checker=jsonschema.Draft7Validator.FORMAT_CHECKER) if path.is_file() else None
        if cache[model] is None:
            return [f"no vendored schema for {model}"]
        return [f"{'/'.join(map(str, e.absolute_path)) or '(entity)'}: {e.message}"
                for e in cache[model].iter_errors(key_values(entity))]
    return errors


def key_values(entity):
    """The entity as keyValues (joinedcontext-deployment tests/open_data.py)."""
    out = {}
    for name, a in entity.items():
        if name == "@context":
            continue
        if not isinstance(a, dict) or "type" not in a:
            out[name] = a
        else:
            out[name] = a.get({"Relationship": "object", "LanguageProperty": "languageMap", "JsonProperty": "json"}.get(a["type"], "value"))
    return out


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
    inside = lambda lon, lat: box[0] <= lon <= box[2] and box[1] <= lat <= box[3]
    for entity in entities:
        vertices = points(value_of(entity, "location") or {})
        # A point lies in the territory; a route or an outline only has to reach it, since a
        # cycle route that starts in the region may end beyond its border (T-4518).
        if vertices and not any(inside(*v) for v in vertices) or len(vertices) == 1 and not inside(*vertices[0]):
            lon, lat = next(v for v in vertices if not inside(*v))
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
        """A 503 is dev's broker failing one request (it asks to try again in a minute): retried
        twice before it counts, so a hiccup never hides a dataset."""
        for wait in (20, 60, None):
            answer = self.once(url, accept)
            if answer[0] != 503 or wait is None:
                return answer
            time.sleep(wait)

    def once(self, url, accept):
        request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "joinedcontext-publish-gate/1"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
                status, headers = response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, status, headers = error.read(), error.code, dict(error.headers or {})
        except (urllib.error.URLError, OSError) as error:
            return 0, {}, {"error": str(error)}
        if accept is None:
            return status, {k.lower(): v for k, v in headers.items()}, raw
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        return status, {k.lower(): v for k, v in headers.items()}, body


def endpoints(http):
    """space -> [(dcat url, types)] of the public Endpoints the hub lists."""
    out, cursor = {}, None
    while True:
        arguments = {"limit": 100} | ({"cursor": cursor} if cursor is not None else {})
        content = hub_page(arguments)
        for e in content.get("endpoints", content) if isinstance(content, dict) else content:
            out.setdefault(e["space"], []).append((e["dcat"], e.get("types", [])))
        cursor = content.get("nextCursor") if isinstance(content, dict) else None
        if cursor is None:
            return out


def hub_page(arguments):
    """One page of the hub's `list_endpoints` (it pages by `nextCursor`)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_endpoints", "arguments": arguments}}
    request = urllib.request.Request(HUB, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read().decode()
    if raw.lstrip().startswith(("event:", "data:")):
        raw = next(line[5:] for line in raw.splitlines() if line.startswith("data:"))
    result = json.loads(raw)["result"]
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def entities_url(base, **params):
    return f"{base}ngsi-ld/v1/entities?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"


def count(http, base, kind, q=None):
    params = {"type": kind, "count": "true", "limit": 0}
    if q:
        params["q"] = q
    status, headers, body = http.get(entities_url(base, **params))
    n = headers.get("ngsild-results-count")
    return (int(n), "") if status == 200 and n is not None else (None, f"the Endpoint answered {status} {(body or {}).get('title', '') if isinstance(body, dict) else ''}".strip())


def kept(e, row):
    """Whether the pipeline keeps a source row: its `rowFilter.keep` column matches the pattern."""
    keep = (e.get("rowFilter") or {}).get("keep")
    return not keep or re.search(keep["pattern"], str(row.get(keep["column"]) or "").strip()) is not None


def every_row(http, e):
    """Every row of a CKAN or Esri source, page by page until an empty one, or None."""
    s, rows = e["source"], []
    while True:
        if s["kind"] == "esri-featureserver":
            params = {"where": (e.get("rowFilter") or {}).get("where", "1=1"), "outFields": "*", "resultOffset": len(rows),
                      "resultRecordCount": 1000, "returnGeometry": "false", "f": "geojson"}
            status, _, body = http.get(f"{s['url']}/query?{urllib.parse.urlencode(params)}")
            page = [f.get("properties") or {} for f in (body or {}).get("features") or []] if status == 200 else None
        elif s["kind"] == "ckan-datastore":
            status, _, body = http.get(f"{CKAN}/api/3/action/datastore_search?resource_id={s['resource']}&limit=32000&offset={len(rows)}")
            page = ((body or {}).get("result") or {}).get("records") if status == 200 else None
        else:
            return None
        if page is None:
            return None
        if not page:
            return rows
        rows += page


def source_count(http, e):
    """The entities the pipeline writes for the source: its rows, less those `rowFilter.keep`
    drops, and for a row-hash id one per distinct set of mapped cells (Research/bb-bbsk-ngsi-ld-mapping.md §4)."""
    s = e["source"]
    if s["kind"] in ("esri-featureserver", "ckan-datastore") and (
            (e.get("rowFilter") or {}).get("keep") or not e["entityId"].get("keys")):
        rows = every_row(http, e)
        if rows is None:
            return None
        rows = [r for r in rows if kept(e, r)]
        if e["entityId"].get("keys"):
            return len(rows)
        cols = sorted({p["column"] for p in e["properties"].values()})
        return len({json.dumps([r.get(c) for c in cols], ensure_ascii=False) for r in rows})
    if s["kind"] == "esri-featureserver":
        where = (e.get("rowFilter") or {}).get("where", "1=1")
        status, _, body = http.get(f"{s['url']}/query?{urllib.parse.urlencode({'where': where, 'returnCountOnly': 'true', 'f': 'json'})}")
        return (body or {}).get("count") if status == 200 else None
    if s["kind"] == "ckan-datastore":
        status, _, body = http.get(f"{CKAN}/api/3/action/datastore_search?resource_id={s['resource']}&limit=0")
        return ((body or {}).get("result") or {}).get("total") if status == 200 else None
    if s["kind"] == "esri-layers":
        counts = [layer_count(http, x) for x in s["layers"]]
        return None if None in counts else sum(counts)
    if s["kind"] == "zip-geojson":
        features = zip_features(http, s)
        return None if features is None else len(features)
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
    if s["kind"] == "esri-layers":
        for x in s["layers"]:
            n = layer_count(http, x)
            if n is None:
                return None, None
            if offset >= n:
                offset -= n
                continue
            params = {"where": x.get("where", "1=1"), "outFields": ",".join(["OBJECTID", *x["fields"]]), "orderByFields": "OBJECTID",
                      "returnGeometry": "true", "outSR": "4326", "geometryPrecision": "6", "f": "geojson",
                      "resultOffset": offset, "resultRecordCount": 1}
            status, _, body = http.get(f"{x['url']}/query?{urllib.parse.urlencode(params)}")
            features = (body or {}).get("features") if status == 200 else None
            if not features:
                return None, None
            f = features[0]
            row = (f.get("properties") or {}) | {"vrstva": x["name"], "vrstva_id": f"{x['key']}-{f.get('id')}",
                                                 "_page": (x["key"], offset // PAGE * PAGE)}
            return row, f.get("geometry")
        return None, None
    if s["kind"] == "zip-geojson":
        # S-JTSK coordinates: the row's values are compared, its location only against the box.
        features = zip_features(http, s)
        return ((features[offset].get("properties") or {}), None) if features and offset < len(features) else (None, None)
    status, _, body = http.get(f"{s['url']}?per_page=1&page={offset + 1}")
    return (body[0], None) if status == 200 and isinstance(body, list) and body else (None, None)


def layer_count(http, x):
    status, _, body = http.get(f"{x['url']}/query?{urllib.parse.urlencode({'where': x.get('where', '1=1'), 'returnCountOnly': 'true', 'f': 'json'})}")
    return (body or {}).get("count") if status == 200 else None


ZIPS = {}


def zip_features(http, s):
    """The features of the archive's GeoJSON member (the generator's pick: the name ending in
    .geojson), read once per run, or None."""
    if s["url"] not in ZIPS:
        status, _, raw = http.get(s["url"], accept=None)
        features = None
        if status == 200 and raw:
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    member = next(n for n in archive.namelist() if n.lower().endswith(".geojson"))
                    features = json.loads(archive.read(member)).get("features")
            except (zipfile.BadZipFile, StopIteration, ValueError, AttributeError):
                features = None
        ZIPS[s["url"]] = features if isinstance(features, list) else None
    return ZIPS[s["url"]]


def part_slug(e, slug, row):
    """The slug of the pipeline that wrote a row: an esri-layers entry is split into pipelines of
    about PART_ROWS rows, `<slug>`, `<slug[:36]>-p2`, … (deployment generate.py `parts`)."""
    if e["source"]["kind"] != "esri-layers" or "_page" not in row:
        return slug
    i, held = 0, 0
    first = True
    for x in e["source"]["layers"]:
        for o in range(max(1, -(-int(x["rows"] * 1.5) // PAGE))):
            rows = max(0, min(PAGE, x["rows"] - o * PAGE))
            if not first and held + rows > PART_ROWS:
                i, held, first = i + 1, 0, True
            if (x["key"], o * PAGE) == row["_page"]:
                return slug if i == 0 else f"{slug[:36].rstrip('-')}-p{i + 1}"
            first = False
            held += rows
    return slug


def find_entity(http, base, e, slug, row, dataset_q):
    ident = entity_id(e, slug, row)
    if ident:
        status, _, body = http.get(f"{base}ngsi-ld/v1/entities/{urllib.parse.quote(ident, safe='')}")
        return body if status == 200 and isinstance(body, dict) else None
    # A row-hash id is the hash of every mapped cell, so the row's entity is the one that carries
    # all of them: match on every string and whole-number cell the q grammar can quote.
    terms = []
    for name, prop in e["properties"].items():
        value = expected(prop["range"], row.get(prop["column"])) if prop["range"] in ("string", "integer") else None
        if isinstance(value, int) and not isinstance(value, bool):
            # The model's range wins in the generator, so an integer column may be stored as text.
            terms.append(f'({attribute(name)}=={value}|{attribute(name)}=="{value}")')
        elif isinstance(value, str) and not re.search(r'["\\;|()]', value) and len(value) <= 200:
            terms.append(f'{attribute(name)}=="{value}"')
    if not terms:
        return None
    status, _, body = http.get(entities_url(base, type=e["type"], q=";".join([t for t in [dataset_q] if t] + terms), limit=1))
    return body[0] if status == 200 and isinstance(body, list) and body else None


# --- one package -------------------------------------------------------------------------------

def gate(package, e, slug, spaces, http, ask, now, shared, sdm=None):
    org = (package.get("organization") or {}).get("name")
    ngsi = [r["url"] for r in package.get("resources", []) if (r.get("format") or "").upper() == "NGSI-LD"]
    checks = {}
    row = {"package": package["name"], "org": org, "type": None, "entities": None, "rows": None}

    if e is None or e["status"] != "mapped":
        if e is not None and e["status"] == "not-ingestible":
            checks["ngsi-ld"] = check("fail", f"not ingestible (T-4324 deletes it): {e.get('reason', '')}")
        elif e is not None and e["status"] in ("removed", "no-data-source"):
            checks["ngsi-ld"] = check("fail", f"{e['status']} in the mapping: {e.get('note') or e.get('reason') or ''}".strip())
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

    # A dataset a platform seed reads whole is that seed's Endpoint and its whole type
    # (od-datasets.json `filtered: false`); that seed's own pipeline names the ids and attributes.
    whole = (e.get("existing") or {}).get("covers") == "whole"
    kind = e.get("doorType", e["type"]) if whole else e["type"]
    row["type"] = kind
    sample = []
    doors = [e["door"]] if whole and e.get("door") else [d for d, types in spaces.get(e["space"], []) if kind in types]
    # A seed whose door two datasets share marks its rows with `isPartOf` (od-datasets.json
    # `filtered: true`, bbsk obce and okresy: T-4523).
    dataset_q = None if whole and not e.get("doorFiltered") else f'isPartOf=="{e["dataset"]}"'
    if not doors and whole:
        # The mapping's space is the generator's default; the seed's own door is in od-datasets.json
        # (bb-podujatia is Event of banskabystrica-verejne, not of the mapping's space: T-4520).
        checks["ngsi-ld"] = check("not run", f"a platform seed reads it whole and its door is in od-datasets.json: pass --distributions")
    elif not doors:
        checks["ngsi-ld"] = check("fail", f"no public Endpoint over {e['space']} lists {kind}")
    else:
        base = doors[0]
        n, why = count(http, base, kind, dataset_q)
        if n is None and shared.get((e["space"], kind), 0) == 1:
            n, why = count(http, base, kind)
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
        status, _, sample = http.get(entities_url(base, type=kind, q=dataset_q, limit=SAMPLE))
        if status != 200 or not isinstance(sample, list):
            status, _, sample = http.get(entities_url(base, type=kind, limit=SAMPLE))
        sample = sample if isinstance(sample, list) else []
        problems += [p for p in (outside(org, sample), None if whole else duplicates(e, sample), stale(e, sample, now)) if p]
        total = 0 if whole else row["rows"] or 0
        for offset in sorted({0, total // 4, total // 2, 3 * total // 4, max(0, total - 1)}) if not whole else ():
            source, geometry = source_row(http, e, offset)
            if source is None:
                problems.append(f"source row {offset} could not be read")
                continue
            if not kept(e, source):
                continue
            entity = find_entity(http, base, e, part_slug(e, slug, source), source, dataset_q)
            if entity is None:
                problems.append(f"source row {offset} has no entity")
                continue
            wrong = row_mismatch(e, source, geometry if e["source"].get("geometry") else None, entity)
            if wrong:
                problems.append(f"row {offset} ({entity.get('id')}): " + "; ".join(wrong[:3]))
        checks["accurate"] = check("fail", " | ".join(problems)) if problems else check("pass")

    model = e.get("smartDataModel")
    if not model:
        sdm = check("fail", "the mapping names no Smart Data Model (T-4319)")
    elif sdm is None or not sample:
        sdm = check("pass")
    else:
        wrong = next(((x.get("id"), errs) for x in sample if (errs := sdm(model, x))), None)
        sdm = check("fail", f"{wrong[0]} is not a valid {model}: " + "; ".join(wrong[1][:3])) if wrong else check("pass")
    if "fail" in (checks["ngsi-ld"]["state"], checks["accurate"]["state"], sdm["state"]):
        checks["queryable"] = check("not run", "asked only when checks 1, 2 and 4 pass (the eval copy's hourly limit)")
    elif not row["entities"]:
        checks["queryable"] = check("not run", "no entity count to ask about")
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

def run(packages, mapping, spaces, http, ask, now, sdm=None, distributions=None):
    by_name = {e["name"]: e for e in mapping}
    for name, d in (distributions or {}).items():
        if name in by_name and "filtered" in d:
            by_name[name] = by_name[name] | {"door": f"{GATEWAY}/api/endpoint/{d['endpoint']}/", "doorType": d["type"],
                                             "doorFiltered": d["filtered"]}
    names = slugs([e for e in mapping if e["status"] == "mapped"])
    shared = {}
    for e in mapping:
        if e["status"] == "mapped":
            shared[(e["space"], e["type"])] = shared.get((e["space"], e["type"]), 0) + 1
    return [gate(p, by_name.get(p["name"]), names.get(p["name"]), spaces, http, ask, now, shared, sdm) for p in packages]


def stubs(names, packages, mapping):
    """The hidden packages, from their mapping entries: the gate reads a mapped package's data
    through the mapping, never through its catalogue record."""
    listed = {p["name"] for p in packages}
    by_name = {e["name"]: e for e in mapping}
    return [{"name": n, "title": (by_name.get(n) or {}).get("title", n),
             "organization": {"name": (by_name.get(n) or {}).get("org")}, "resources": []}
            for n in dict.fromkeys(names) if n not in listed]


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
    parser.add_argument("--sdm", type=Path, help="the vendored Smart Data Models (deployment scripts/open-data/sdm)")
    parser.add_argument("--distributions", type=Path,
                        help="deployment charts/ckan/files/od-datasets.json: the Endpoint of a dataset a seed reads whole")
    parser.add_argument("--also", type=Path, help="package names the public search hides, one per line")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    if not args.mapping or not args.out or args.sdm and not (args.sdm / "common-schema.json").is_file():
        parser.print_usage()
        return 2
    http = Http()
    status, _, body = http.get(f"{args.catalogue}/api/3/action/package_search?rows=1000")
    if status != 200:
        sys.exit(f"the catalogue answered {status}")
    mapping = yaml.safe_load(args.mapping.read_text())["datasets"]
    packages = body["result"]["results"]
    if args.also:
        packages += stubs(args.also.read_text().split(), packages, mapping)
    packages = sorted(packages, key=lambda p: p["name"])
    ask = None if args.eval == "none" else eval_asker(GATEWAY, args.eval)
    now = dt.datetime.now(dt.timezone.utc)
    rows = run(packages, mapping, endpoints(http), http, ask, now, args.sdm and sdm_validators(args.sdm),
               args.distributions and json.loads(args.distributions.read_text()))
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
         "dataset": "https://cat/dataset/bb-skoly", "refresh": "P1D", "observedAt": "updated", "smartDataModel": "dataModel.Education/School",
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

    folder = Path(tempfile.mkdtemp())
    (folder / "Education/School").mkdir(parents=True)
    (folder / "common-schema.json").write_text(json.dumps({"definitions": {"Count": {"type": "integer", "minimum": 0}}}))
    (folder / "Education/School/schema.json").write_text(json.dumps({"type": "object", "required": ["id", "type"], "properties": {
        "type": {"enum": ["School"]}, "ziaci": {"$ref": COMMON[0] + "#/definitions/Count"}}}))
    (folder / "extensions/Grant").mkdir(parents=True)
    (folder / "extensions/Grant/schema.json").write_text(json.dumps({"type": "object", "properties": {"type": {"enum": ["Grant"]}}}))
    models = sdm_validators(folder)
    assert models("extension/Grant", {"id": "g", "type": "Grant"}) == [] and models("extension/Grant", {"id": "g", "type": "School"})

    def verdict_of(http, ask=right, pkg=package, entry=e, sdm=models):
        return gate(pkg, entry, slug, spaces, http, ask, now, {("bb-od", "School"): 1}, sdm)

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
        "not valid in its model": verdict_of(world(), sdm=lambda m, x: models(m, dict(x, ziaci={"type": "Property", "value": -1}))),
        "no vendored schema": verdict_of(world(), entry=dict(e, smartDataModel="dataModel.Education/Library")),
    }
    for name, result in cases.items():
        assert result["verdict"] == "hide", (name, result)
    assert "5 entities for 3 source rows" in cases["count mismatch"]["checks"]["accurate"]["reason"]
    assert "ziaci" in cases["a wrong value"]["checks"]["accurate"]["reason"], cases["a wrong value"]
    assert "outside" in cases["outside the box"]["checks"]["accurate"]["reason"]
    # The region's own west edge (Žarnovica, 18.53771 E) is inside bbsk's box; Bratislava is not.
    assert outside("bbsk", [entity_of(source[0], location={"type": "GeoProperty", "value": {"type": "Point", "coordinates": [18.53771, 48.313064]}})]) == ""
    assert outside("bbsk", [entity_of(source[0], location={"type": "GeoProperty", "value": {"type": "Point", "coordinates": [17.338519, 48.650915]}})])
    route = lambda *xy: [entity_of(source[0], location={"type": "GeoProperty", "value": {"type": "LineString", "coordinates": list(xy)}})]
    assert outside("bbsk", route([18.6, 48.4], [18.37, 48.6])) == "", "a route leaving the region"
    assert "outside" in outside("bbsk", route([17.2, 48.4], [17.3, 48.6])), "a route nowhere near it"
    assert "same" in cases["a duplicate id"]["checks"]["accurate"]["reason"]
    assert "older than a day" in cases["stale observedAt"]["checks"]["accurate"]["reason"]
    assert "ziaci: -1 is less than the minimum of 0" in cases["not valid in its model"]["checks"]["smart-data-model"]["reason"], cases["not valid in its model"]
    assert "no vendored schema" in cases["no vendored schema"]["checks"]["smart-data-model"]["reason"]
    assert verdict_of(world(), sdm=None)["verdict"] == "keep"
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
    # A hidden package is gated from its mapping; a listed one is not stubbed twice; a removed one says why.
    hidden = stubs(["bb-skoly", "bb-skoly", "bb-gone"], [], [e, {"name": "bb-gone", "status": "removed", "note": "no model"}])
    assert [p["name"] for p in hidden] == ["bb-skoly", "bb-gone"] and hidden[0]["organization"]["name"] == "banskabystrica", hidden
    assert stubs(["bb-skoly"], [package], [e]) == []
    assert verdict_of(world(), pkg=hidden[0])["verdict"] == "keep"
    gone = gate(hidden[1], {"name": "bb-gone", "status": "removed", "note": "no model"}, None, spaces, world(), right, now, {})
    assert gone["verdict"] == "hide" and gone["checks"]["ngsi-ld"]["reason"] == "removed in the mapping: no model", gone
    # An esri-layers row is found under the slug of the pipeline its page went to (generate.py `parts`).
    layers = {"source": {"kind": "esri-layers", "layers": [{"key": "dtm-1", "rows": 500}, {"key": "dtm-2", "rows": 8546}]}}
    assert part_slug(layers, "zelen", {"_page": ("dtm-1", 0)}) == "zelen"
    assert part_slug(layers, "zelen", {"_page": ("dtm-2", 2000)}) == "zelen"
    assert part_slug(layers, "zelen", {"_page": ("dtm-2", 3000)}) == "zelen-p2"
    assert part_slug(layers, "zelen", {"_page": ("dtm-2", 12000)}) == "zelen-p3"
    assert part_slug(e, "skoly", {"_page": ("x", 0)}) == "skoly"
    # A zip-geojson source: its GeoJSON member's features, counted and read by offset; a broken archive answers nothing.
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("readme.txt", "x")
        archive.writestr("ADRESY.GeoJSON", json.dumps({"features": [{"properties": {"ID": 1}}, {"properties": {"ID": 2}}]}))

    class Zip:
        def get(self, url, accept=None):
            return (200, {}, packed.getvalue()) if url == "z1" else (200, {}, b"not a zip")
    z = {"kind": "zip-geojson", "url": "z1"}
    assert source_count(Zip(), {"source": z}) == 2 and source_row(Zip(), {"source": z}, 1) == ({"ID": 2}, None)
    assert source_row(Zip(), {"source": z}, 2) == (None, None)
    assert source_count(Zip(), {"source": {"kind": "zip-geojson", "url": "z2"}}) is None
    # The hub's endpoint list is read to its last page.
    global hub_page
    pages = {None: {"endpoints": [{"space": "a", "dcat": "d1"}], "nextCursor": "2"}, "2": {"endpoints": [{"space": "a", "dcat": "d2", "types": ["T"]}]}}
    real, hub_page = hub_page, lambda arguments: pages[arguments.get("cursor")]
    try:
        assert endpoints(None) == {"a": [("d1", []), ("d2", ["T"])]}
    finally:
        hub_page = real
    # A 503 is retried; a second answer counts.
    calls = []

    class Flaky(Http):
        def once(self, url, accept):
            calls.append(url)
            return (503, {}, None) if len(calls) == 1 else (200, {}, [])
    real_sleep, time.sleep = time.sleep, lambda s: None
    try:
        assert Flaky().get("u")[0] == 200 and len(calls) == 2
    finally:
        time.sleep = real_sleep
    # A row-hash entity is matched on its whole-number cells as number or text.
    asked = []

    class Q:
        def get(self, url, accept=None):
            asked.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0])
            return 200, {}, [{"id": "x"}]
    hashed = dict(e, entityId={"rowHash": True})
    assert find_entity(Q(), base, hashed, slug, source[0], "isPartOf==\"d\"") == {"id": "x"}
    assert '(ziaci==410|ziaci=="410")' in asked[0] and 'nazov=="ZŠ Moskovská"' in asked[0], asked
    # A column called like an attribute the generator sets is renamed; `name` is the model's own.
    assert attribute("source") == "columnSource" and attribute("isPartOf") == "columnIsPartOf" and attribute("name") == "name"
    # A dataset a seed reads whole is counted and sampled over its type, with no isPartOf filter.
    seen = []

    class Whole:
        def get(self, url, accept=None):
            seen.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
            return world().get(url, accept)
    whole = verdict_of(Whole(), entry=dict(e, existing={"seed": "x.yaml", "covers": "whole"}, door="https://gw/api/endpoint/seed/", doorType="School"))
    assert whole["verdict"] == "keep" and whole["entities"] == 3, whole
    assert not any("q" in qs and "isPartOf" in qs["q"][0] for qs in seen), seen
    door = run([package], [dict(e, existing={"covers": "whole"})], spaces, world(), right, now, None,
               {"bb-skoly": {"endpoint": "seed", "type": "School", "filtered": False}})
    assert door[0]["entities"] == 3, door
    # A shared door is read for this dataset's rows alone.
    seen.clear()
    shared = run([package], [dict(e, existing={"covers": "whole"})], spaces, Whole(), right, now, None,
                 {"bb-skoly": {"endpoint": "seed", "type": "School", "filtered": True}})
    assert shared[0]["entities"] == 3 and any("isPartOf" in qs.get("q", [""])[0] for qs in seen), (shared, seen)
    # Without its door a whole seed's dataset is held, not hidden for a space it does not live in.
    doorless = verdict_of(world(), entry=dict(e, space="elsewhere", existing={"seed": "x.yaml", "covers": "whole"}))
    assert doorless["verdict"] == "hold" and "--distributions" in doorless["checks"]["ngsi-ld"]["reason"], doorless
    # The source counts what the pipeline keeps: rows a keep pattern passes, and for a row hash
    # one per distinct set of mapped cells (T-4517).
    schools = dict(e, rowFilter={"keep": {"column": "Nazov", "pattern": "^ZŠ "}})
    assert source_count(world(), schools) == 2
    assert verdict_of(world(entities=[entity_of(r) for r in source[:2]]), entry=schools)["checks"]["accurate"]["state"] == "pass"
    assert "3 entities for 2 source rows" in verdict_of(world(), entry=schools)["checks"]["accurate"]["reason"]
    twice = source + [dict(source[2])]
    assert source_count(world(rows=twice), hashed) == 3 and source_count(world(rows=twice), e) == 4
    assert source_count(world(rows=twice + [dict(source[2], ID=9)]), hashed) == 4
    print("publish_gate selftest: ok")


if __name__ == "__main__":
    sys.exit(main())

"""Validate one published CKAN dataset against its Endpoint, end to end, read only (T-4087).

For a dataset it checks, one line each, PASS/FAIL with the measured number (WARN and SKIP say what
was not held against it and why):

1. ckan: package_show, the `endpoint` extra on the installation's host, every resource URL
   answering (MCP: an `initialize` over POST; the WebSocket under 6);
2. ngsi: `/types` lists the types, and per type its count, its ids (`urn:ngsi-ld:<Type>:`, no
   duplicates, type equal), its values (no empty, "null" or NaN, no future timestamp, no
   negative count, available <= total) and its location (valid GeoJSON in range; outside the
   publisher's area is a WARN: HSL, PID and region polygons reach past a city);
3. fresh: per type the newest write (`modifiedAt`, `observedAt`, `dateObserved`, ...) against the
   period of the Pipeline that writes it, from the DataSource manifests of the deployment seed;
4. rep: file.csv, file.json, file.geojson and file.xlsx per type, the OGC items' numberMatched and
   the CKAN DataStore total, each within 1 % of the NGSI-LD count, each export within 10 s;
5. schema: schema/index.json and every artifact it lists answer, match their sha256 and parse;
6. ws: x-subscribe answers 201, a live type sends an x-notification within the window when it
   changed in it, an invalid q answers x-result 400;
7. ogc: landing, conformance and collections answer, items of a located type carry geometry;
8. source: 5 random entities per type found in the source the DataSource names (an id or a value
   of the entity occurs in its body); skipped with the reason where the source needs a key,
   is an archive member or a stream.

Only GET requests, the MCP `initialize` and a WebSocket subscription are sent. A non-public
Endpoint is read with `JC_GATEWAY_TOKEN` (a ServiceAccount token), sent only to the
installation's host and only after an anonymous 401/403; it is never printed or written.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import re
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import ws

# A dataset's expected types and their freshness, one file per dataset (T-4088..T-4099).
EXPECTED_DIR = Path(__file__).resolve().parent / "expected"
SEED_DIR = Path(
    os.environ.get("SEED_DIR", "/workspace/joinedcontext-deployment/components/context-gateway/seed")
)
# The seed namespaces whose Pipelines write a CKAN organization's data; others are named alike.
ORG_NAMESPACES = {"hel-fi": ("helsinki", "helsinki-demo-feeds")}
# A generous lon/lat box per publisher: outside it is a WARN, never a FAIL.
AREA = {
    "hel-fi": (24.0, 26.0, 59.8, 60.7),
    "praha": (14.1, 14.9, 49.9, 50.2),
    "zilina": (18.5, 19.0, 49.0, 49.4),
    "banskabystrica": (18.9, 19.4, 48.6, 48.85),
    "bbsk": (18.5, 20.7, 47.9, 49.2),
}
WRITE_TIMES = ("observedAt", "dateObserved", "dateModified", "dateLastReported", "modifiedAt")
NONNEG = re.compile(r"(count|number|capacity|spot|total|available|free|occupancy|length|area|population|price)", re.I)
BOUNDED = (("availableSpotNumber", "totalSpotNumber"), ("freeSlotNumber", "totalSlotNumber"))
EXPORT_SECONDS = 10
LIVE_SECONDS = 600
SCAN_CAP = 20000
# Compact, as the MCP SDKs send it: dev's /mcp hangs on the same body with spaces (chyby.md).
MCP_INIT = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "jc-datasets", "version": "1"}},
}, separators=(",", ":")).encode()


@dataclass
class Resp:
    status: int | None
    headers: dict
    body: bytes
    secs: float


@dataclass
class Check:
    dataset: str
    check: str
    verdict: str  # PASS, FAIL, WARN, SKIP
    measured: str
    detail: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Http:
    """The network: GET (and the one MCP POST) over urllib, a WebSocket probe over `ws`."""

    def __init__(self, token: str | None = None, token_host: str | None = None):
        self._token, self._token_host = token, token_host
        self._plain = urllib.request.build_opener()
        # A token request never follows a redirect, so the token never reaches another host.
        self._bearer = urllib.request.build_opener(_NoRedirect)

    def _may_send_token(self, url: str) -> bool:
        return bool(self._token) and urlsplit(url).hostname == self._token_host

    def fetch(self, url, accept="*/*", method="GET", data=None, headers=None, limit=None, timeout=60) -> Resp:
        resp = self._once(url, accept, method, data, headers, limit, timeout, None)
        if resp.status in (401, 403) and self._may_send_token(url):
            resp = self._once(url, accept, method, data, headers, limit, timeout, self._token)
        return resp

    def _once(self, url, accept, method, data, headers, limit, timeout, token) -> Resp:
        sent = {"Accept": accept, "User-Agent": "jc-conformance-datasets", **(headers or {})}
        if data is not None:
            sent["Content-Type"] = "application/json"
        if token:
            sent["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, method=method, headers=sent)
        opener = self._bearer if token else self._plain
        start = time.monotonic()
        try:
            with opener.open(request, timeout=timeout) as r:
                body = r.read(limit) if limit else r.read()
                return Resp(r.status, {k.lower(): v for k, v in r.headers.items()}, body, time.monotonic() - start)
        except urllib.error.HTTPError as e:
            return Resp(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(2000), time.monotonic() - start)
        except (OSError, ValueError) as e:  # timeout, DNS, reset, a bad URL
            return Resp(None, {}, str(e).encode(), time.monotonic() - start)

    def ws(self, url: str, subscription: dict, seconds: float) -> tuple[int, int | None, int]:
        answer = ws.probe(url, subscription, seconds)
        if answer[0] in (401, 403) and self._may_send_token(url):
            answer = ws.probe(url, subscription, seconds, self._token)
        return answer


# ---- the seed: which Pipeline writes a type, how often, from which DataSource ----

@dataclass
class Feed:
    period: float  # seconds between writes; a stream counts as 60
    sources: list  # DataSource specs


def duration_seconds(text) -> float | None:
    m = re.fullmatch(r"(\d+)\s*([smhd])", str(text or "").strip())
    return int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]] if m else None


def cron_seconds(expr) -> float | None:
    fields = str(expr or "").split()
    if len(fields) != 5:
        return None
    minute, hour, dom, _, dow = fields
    if minute.startswith("*/"):
        return int(minute[2:]) * 60
    if minute == "*":
        return 60
    if hour.startswith("*/"):
        return int(hour[2:]) * 3600
    if hour == "*":
        return 3600
    if dow != "*":
        return 7 * 86400
    return 31 * 86400 if dom != "*" else 86400


def load_feeds(root: Path, namespaces) -> dict[str, Feed]:
    """Per entity type, the shortest period of the Pipelines writing it and their DataSources.

    A Pipeline names its type in `output.type`, or, writing several, in the mapping of the
    `-bento.yaml` beside it."""
    import yaml  # the conformance requirements pin it; only the seed reading needs it

    feeds: dict[str, Feed] = {}
    for ns in namespaces:
        folder = root / ns
        if not folder.is_dir():
            continue
        docs = []
        for path in sorted(folder.glob("*.yaml")):
            try:
                docs += [(path, d) for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(d, dict)]
            except yaml.YAMLError:
                continue  # a bento mapping or a template, not a manifest
        sources = {d["metadata"]["name"]: d.get("spec", {}) for _, d in docs if d.get("kind") == "DataSource"}
        for path, d in docs:
            if d.get("kind") != "Pipeline":
                continue
            spec = d.get("spec") or {}
            source = sources.get(((spec.get("source") or {}).get("dataSourceRef") or {}).get("name"))
            period = duration_seconds(spec.get("period")) or cron_seconds(spec.get("schedule"))
            if period is None:
                if not source or source.get("type") not in ("mqtt", "nats"):
                    continue
                period = 60
            types = {(spec.get("output") or {}).get("type")}
            companion = path.with_name(path.stem + "-bento.yaml")
            if companion.is_file():
                types |= set(re.findall(r'"type"\s*:\s*"([A-Z][A-Za-z0-9]+)"', companion.read_text(encoding="utf-8")))
            for t in types - {None}:
                feed = feeds.setdefault(t, Feed(period, []))
                feed.period = min(feed.period, period)
                if source and source not in feed.sources:
                    feed.sources.append(source)
    return feeds


# ---- reading the data ----

def value(attr):
    if isinstance(attr, dict):
        for key in ("value", "object", "languageMap", "json"):
            if key in attr:
                return attr[key]
    return attr


def parse_time(v) -> dt.datetime | None:
    if isinstance(v, dict) and "@value" in v:
        v = v["@value"]
    if not isinstance(v, str):
        return None
    try:
        t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def points(geometry):
    def walk(x):
        if isinstance(x, list) and len(x) >= 2 and all(isinstance(n, (int, float)) for n in x[:2]):
            yield x
        elif isinstance(x, list):
            for y in x:
                yield from walk(y)
    yield from walk(geometry.get("coordinates") if isinstance(geometry, dict) else None)


def scan(http: Http, base: str, typ: str, cap: int = SCAN_CAP):
    """The type's entities with their system times (at most `cap`), its count, or the error."""
    items, total = [], None
    while True:
        r = http.fetch(
            f"{base}ngsi-ld/v1/entities?type={quote(typ)}&limit=1000&offset={len(items)}&count=true&options=sysAttrs",
            "application/json",
        )
        if r.status != 200:
            return items, total, f"HTTP {r.status} {r.body[:160]!r}"
        page = json.loads(r.body)
        if total is None:
            total = int(r.headers.get("ngsild-results-count", len(page)))
        items += page
        if not page or len(items) >= min(total, cap):
            return items, total, None


def newest_write(entity) -> dt.datetime | None:
    times = [parse_time(entity.get("modifiedAt"))]
    for key, attr in entity.items():
        if key in WRITE_TIMES:
            times.append(parse_time(value(attr)))
        if isinstance(attr, dict):
            times += [parse_time(attr.get("modifiedAt")), parse_time(attr.get("observedAt"))]
    return max((t for t in times if t), default=None)


def quality(org: str, typ: str, items: list, now: dt.datetime) -> dict:
    """What is wrong with the type's entities: kind -> (count, an example id), per check."""
    ids = Counter(e.get("id") for e in items)
    found = {"ids": Counter(), "values": Counter(), "location": Counter(), "area": Counter()}
    example: dict = {}

    def flag(check, kind, eid):
        found[check][kind] += 1
        example.setdefault((check, kind), eid)

    for eid, n in ids.items():
        if n > 1:
            flag("ids", "duplicate id", eid)
    box, located = AREA.get(org), 0
    for e in items:
        eid = str(e.get("id", "?"))
        if not eid.startswith(f"urn:ngsi-ld:{typ}:"):
            flag("ids", f"id not urn:ngsi-ld:{typ}:", eid)
        if e.get("type") != typ:
            flag("ids", f"type {e.get('type')}", eid)
        for key, attr in e.items():
            if key in ("id", "type", "@context", "createdAt", "modifiedAt", "location"):
                continue
            v = value(attr)
            if v is None or v == "" or v == [] or v == {}:
                flag("values", f"empty {key}", eid)
            elif isinstance(v, str) and v.strip().lower() in ("null", "none", "nan", "undefined"):
                flag("values", f"literal {v.strip()!r} in {key}", eid)
            elif isinstance(v, float) and not math.isfinite(v):
                flag("values", f"NaN in {key}", eid)
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0 and NONNEG.search(key):
                flag("values", f"negative {key}", eid)
            stamps = [parse_time(v)] if key in WRITE_TIMES else []
            if isinstance(attr, dict):
                stamps.append(parse_time(attr.get("observedAt")))
            if any(t and t > now + dt.timedelta(minutes=10) for t in stamps):
                flag("values", f"{key} in the future", eid)
        for available, total in BOUNDED:
            a, t = value(e.get(available)), value(e.get(total))
            if isinstance(a, (int, float)) and isinstance(t, (int, float)) and a > t:
                flag("values", f"{available} > {total}", eid)
        if "location" not in e:
            continue
        located += 1
        geometry = value(e["location"])
        pts = list(points(geometry))
        if not isinstance(geometry, dict) or not geometry.get("type") or not pts:
            flag("location", "not GeoJSON with coordinates", eid)
            continue
        for lon, lat, *_ in pts:
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                flag("location", "coordinates out of range", eid)
                break
            if lon == 0 and lat == 0:
                flag("location", "location at 0,0", eid)
                break
            if box and not (box[0] <= lon <= box[1] and box[2] <= lat <= box[3]):
                swapped = box[0] <= lat <= box[1] and box[2] <= lon <= box[3]
                flag("location" if swapped else "area", "lat/lon swapped" if swapped else f"outside the {org} area", eid)
                break
    return {"found": found, "example": example, "located": located}


def summary(counter: Counter, example: dict, check: str) -> str:
    return "; ".join(f"{kind}: {n} (e.g. {example[(check, kind)]})" for kind, n in counter.most_common(3))


def count_csv(body: bytes) -> int:
    return max(0, sum(1 for _ in csv.reader(io.StringIO(body.decode("utf-8-sig")))) - 1)


def count_json(body: bytes) -> int:
    data = json.loads(body)
    return len(data if isinstance(data, list) else data.get("features") or data.get("items") or [])


def count_geojson(body: bytes) -> int:
    return len(json.loads(body)["features"])


def count_xlsx(body: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(body)) as book:
        sheet = book.read("xl/worksheets/sheet1.xml")
    return max(0, len(re.findall(rb"<row[ >]", sheet)) - 1)


EXPORTS = (("csv", "file.csv", count_csv), ("json", "file.json", count_json),
           ("geojson", "file.geojson", count_geojson), ("xlsx", "file.xlsx", count_xlsx))


def parses(name: str, body: bytes) -> bool:
    try:
        if name.endswith((".json", ".jsonld")):
            json.loads(body)
        elif name.endswith((".yaml", ".yml")):
            import yaml
            yaml.safe_load(body)
        elif name.endswith(".ttl"):
            try:
                import rdflib
            except ImportError:  # not in the pinned requirements: a prefix line is the floor
                return b"@prefix" in body or b"PREFIX" in body
            rdflib.Graph().parse(data=body.decode("utf-8"), format="turtle")
        return bool(body.strip())
    except Exception:  # noqa: BLE001 - any parser's complaint is the same verdict
        return False


def source_tokens(entity) -> set[str]:
    """Strings that would occur in the source if the entity came from it: the id's last segment
    and every scalar value of five characters or more, as text and as JSON-escaped text."""
    tail = str(entity.get("id", "")).rsplit(":", 1)[-1]
    # A minted id prefixes the source's key with the source's name: `chmi-ahol`, `zsr-5618255`.
    tokens = {tail, tail.split("-", 1)[-1]}
    for key, attr in entity.items():
        if key in ("id", "type", "@context", "createdAt", "modifiedAt", "location"):
            continue
        v = value(attr)
        for x in (v.values() if isinstance(v, dict) else [v]):
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                x = repr(x)
            if isinstance(x, str) and len(x) >= 5 and parse_time(x) is None:
                tokens |= {x, json.dumps(x)[1:-1]}
    return {t.lower() for t in tokens if len(t) >= 3}


# ---- one dataset ----

def load_expected(folder: Path, name: str) -> dict:
    """`{type: {"maxAge": "20m" | null, "numeric": [attribute, ...]}}` from `<folder>/<name>.json`, or {}
    when it has none. `numeric` names the Attributes whose value must be a number on every entity."""
    path = folder / f"{name}.json"
    if not path.is_file():
        return {}
    types = json.loads(path.read_text(encoding="utf-8"))["types"]
    for typ, spec in types.items():
        if spec.get("maxAge") is not None and duration_seconds(spec["maxAge"]) is None:
            raise ValueError(f"{path}: {typ} maxAge {spec['maxAge']!r} is not <n>s|m|h|d")
        numeric = spec.get("numeric", [])
        if not isinstance(numeric, list) or not all(isinstance(a, str) and a for a in numeric):
            raise ValueError(f"{path}: {typ} numeric {numeric!r} is not a list of attribute names")
    return types


def validate(http: Http, name: str, base_url: str, ckan_url: str, seed_root: Path = SEED_DIR,
             ws_seconds: float = 60, now: dt.datetime | None = None, expected_dir: Path = EXPECTED_DIR) -> list[Check]:
    out: list[Check] = []

    def add(check, verdict, measured, detail=""):
        out.append(Check(name, check, verdict, str(measured), detail))

    try:
        expected = load_expected(expected_dir, name)
        _validate(http, name, base_url.rstrip("/"), ckan_url.rstrip("/"), seed_root, ws_seconds, now, expected, add)
    except Exception as error:  # noqa: BLE001 - a crash is a red line, never a silent stop
        add("error", "FAIL", type(error).__name__, str(error)[:200])
    return out


def _validate(http, name, base_url, ckan_url, seed_root, ws_seconds, now, expected, add):
    r = http.fetch(f"{ckan_url}/api/3/action/package_show?id={quote(name)}", "application/json")
    if r.status != 200:
        add("ckan.package_show", "FAIL", f"HTTP {r.status}")
        return
    pkg = json.loads(r.body)["result"]
    resources = pkg.get("resources", [])
    add("ckan.package_show", "PASS", f"{len(resources)} resources")
    org = (pkg.get("organization") or {}).get("name", "")
    extras = {e["key"]: e["value"] for e in pkg.get("extras", [])}
    endpoint = extras.get("endpoint", "")
    want = urlsplit(base_url).hostname
    host = urlsplit(endpoint).hostname
    add("ckan.endpoint_host", "PASS" if host == want else "FAIL", host or "no endpoint extra", f"expected {want}")
    if not endpoint:
        return
    # Read the Endpoint on the installation's host, whatever host the extra names.
    base = f"{base_url}{urlsplit(endpoint).path.rstrip('/')}/"

    for res in resources:
        url, fmt = res.get("url", ""), res.get("format", "")
        label = f"ckan.resource {res.get('name') or fmt}"
        if url.rstrip("/").endswith("/ws"):
            continue  # the WebSocket is checked under ws
        if url.rstrip("/").endswith("/mcp"):
            r = http.fetch(url, "application/json, text/event-stream", "POST", MCP_INIT, limit=65536)
        else:
            r = http.fetch(url, limit=65536)
        ok = r.status == 200 and bool(r.body)
        add(label, "PASS" if ok else "FAIL", f"HTTP {r.status} in {r.secs:.1f}s", "" if ok else url)

    r = http.fetch(base + "ngsi-ld/v1/types", "application/json")
    listed = []
    if r.status == 200:
        body = json.loads(r.body)
        raw = body.get("typeList", []) if isinstance(body, dict) else [t.get("typeName") or t.get("id") for t in body]
        listed = [str(t).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for t in raw if t]
    index_r = http.fetch(base + "schema/index.json", "application/json")
    index = json.loads(index_r.body) if index_r.status == 200 else {}
    datastore = {res["name"]: res for res in resources if res.get("datastore_active")}
    declared = {t for m in index.get("models", []) for t in m.get("types", [])} | set(datastore)
    add("ngsi.types", "PASS" if listed else "FAIL", f"HTTP {r.status}, {len(listed)} types", ", ".join(listed))
    missing = sorted(declared - set(listed))
    if listed and missing:
        add("ngsi.types.declared", "WARN", f"{len(missing)} declared types not listed", ", ".join(missing))
    types = listed or sorted(declared)
    # Without /types a type is only declared: one with no entity yet is a WARN, not a FAIL.
    declared_only = not listed
    if expected:
        absent = sorted(set(expected) - set(types))
        add("expected.types", "FAIL" if absent else "PASS",
            f"{len(expected) - len(absent)}/{len(expected)} expected types served", ", ".join(absent))

    feeds = load_feeds(seed_root, ORG_NAMESPACES.get(org, (org,))) if seed_root.is_dir() else {}
    live = [t for t in types if t in feeds and feeds[t].period <= LIVE_SECONDS]
    with ThreadPoolExecutor(1) as background:
        ws_lines = background.submit(_websocket, http, base, types, live, ws_seconds)
        _schema(http, base, index_r, index, add)
        collections = _ogc_landing(http, base, add)
        now = now or dt.datetime.now(dt.timezone.utc)
        rng = random.Random(name)
        source_cache: dict = {}
        for typ in types:
            try:
                _type(http, base, org, typ, feeds, collections, datastore, resources, ckan_url,
                      seed_root, now, rng, source_cache, declared_only, expected.get(typ, {}), add)
            except Exception as error:  # noqa: BLE001 - one type's crash leaves the others checked
                add(f"ngsi.{typ}", "FAIL", type(error).__name__, str(error)[:200])
        for line in ws_lines.result():
            add(*line)


def _type(http, base, org, typ, feeds, collections, datastore, resources, ckan_url, seed_root, now, rng, cache,
          declared_only, expect, add):
    items, total, error = scan(http, base, typ)
    if error:
        add(f"ngsi.{typ}.entities", "FAIL", error)
        return
    if total:
        add(f"ngsi.{typ}.count", "PASS", total)
    else:
        add(f"ngsi.{typ}.count", "WARN" if declared_only else "FAIL", 0,
            "a declared type with no entity" if declared_only else "a listed type with no entity")
    q = quality(org, typ, items, now)
    found, example = q["found"], q["example"]
    for check in ("ids", "values"):
        bad = sum(found[check].values())
        add(f"ngsi.{typ}.{check}", "FAIL" if bad else "PASS", f"{bad} problems in {len(items)}",
            summary(found[check], example, check))
    # A value the expectations say is a number: a dataset of "not measured" texts is not one (T-4081).
    numeric = expect.get("numeric", [])
    if numeric:
        def number(e, a):
            v = e.get(a)
            v = v.get("value") if isinstance(v, dict) else v
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        bad = [f"{e.get('id')} {a}" for e in items for a in numeric if not number(e, a)]
        add(f"ngsi.{typ}.numeric", "FAIL" if bad else "PASS", f"{len(bad)} not a number in {len(items)}",
            ", ".join(bad[:3]))
    if not q["located"]:
        add(f"ngsi.{typ}.location", "SKIP", "no location")
    elif found["location"]:
        add(f"ngsi.{typ}.location", "FAIL", f"{sum(found['location'].values())} bad of {q['located']}",
            summary(found["location"], example, "location"))
    elif found["area"]:
        add(f"ngsi.{typ}.location", "WARN", f"{sum(found['area'].values())} outside the area of {q['located']}",
            summary(found["area"], example, "area"))
    else:
        add(f"ngsi.{typ}.location", "PASS", f"{q['located']} located")

    # 3. freshness
    feed = feeds.get(typ)
    newest = max((t for t in map(newest_write, items) if t), default=None)
    age = (now - newest).total_seconds() if newest else None
    max_age = duration_seconds(expect.get("maxAge"))
    if newest is None:
        add(f"fresh.{typ}", "FAIL" if max_age else "SKIP", "no time attribute",
            f"expected within {expect['maxAge']}" if max_age else "")
    elif max_age:
        add(f"fresh.{typ}", "PASS" if age <= max_age else "FAIL", f"newest {hours(age)} ago",
            f"expected within {expect['maxAge']} (tests/datasets/expected)")
    elif feed is None:
        reason = "no Pipeline in the seed writes it" if seed_root.is_dir() else f"no seed at {seed_root}"
        add(f"fresh.{typ}", "SKIP", f"newest {hours(age)} ago", reason)
    else:
        allowed = feed.period * 1.5 + 600
        add(f"fresh.{typ}", "PASS" if age <= allowed else "FAIL", f"newest {hours(age)} ago",
            f"allowed {hours(allowed)} for a {hours(feed.period)} schedule")

    # 4. every representation counts what NGSI-LD counts
    is_live = bool(feed and feed.period <= LIVE_SECONDS)
    # A live type moves between two reads: one entity of slack beside the 1 %.
    tolerance = max((total or 0) * 0.01, 1 if is_live else 0)
    served = [res.get("url", "").rstrip("/") for res in resources]
    for label, path, counter in EXPORTS:
        if not any(url.endswith(path) for url in served):
            continue
        r = http.fetch(f"{base}{path}?type={quote(typ)}", timeout=60)
        if r.status != 200:
            add(f"rep.{typ}.{label}", "FAIL", f"HTTP {r.status} in {r.secs:.1f}s")
            continue
        try:
            n = counter(r.body)
        except Exception as error:  # noqa: BLE001
            add(f"rep.{typ}.{label}", "FAIL", "unreadable", f"{type(error).__name__}: {str(error)[:120]}")
            continue
        ok = abs(n - total) <= tolerance and r.secs <= EXPORT_SECONDS
        add(f"rep.{typ}.{label}", "PASS" if ok else "FAIL", f"{n} vs {total} in {r.secs:.1f}s",
            "results restricted" if r.headers.get("ngsild-results-restricted") else "")
    if typ in collections:
        r = http.fetch(f"{base}ogc/features/collections/{quote(typ)}/items?limit=1000", "application/geo+json")
        page = json.loads(r.body) if r.status == 200 else {}
        n = page.get("numberMatched")
        if n is None and r.status == 200:
            n = ogc_walk(http, page)
        ok = isinstance(n, int) and abs(n - total) <= tolerance and r.secs <= EXPORT_SECONDS
        add(f"rep.{typ}.ogc", "PASS" if ok else "FAIL", f"{n} vs {total} in {r.secs:.1f}s", "" if r.status == 200 else f"HTTP {r.status}")
        features = page.get("features", [])
        bare = [f.get("id") for f in features if not f.get("geometry")]
        if features:
            add(f"ogc.{typ}.geometry", "FAIL" if bare else "PASS", f"{len(bare)} of {len(features)} without geometry",
                ", ".join(map(str, bare[:3])))
        else:
            add(f"ogc.{typ}.geometry", "SKIP", "no feature on the first page")
    elif q["located"]:
        add(f"ogc.{typ}.collection", "FAIL", "not in /collections", "a located type")
    if typ in datastore:
        r = http.fetch(f"{ckan_url}/api/3/action/datastore_search?resource_id={datastore[typ]['id']}&limit=0", "application/json")
        rows = json.loads(r.body)["result"]["total"] if r.status == 200 else None
        ok = rows is not None and abs(rows - total) <= tolerance
        add(f"rep.{typ}.datastore", "PASS" if ok else "FAIL", f"{rows} vs {total}", "" if r.status == 200 else f"HTTP {r.status}")

    # 8. the source
    if feed is None:
        add(f"source.{typ}", "SKIP", "no DataSource", "no Pipeline in the seed writes it")
        return
    bodies, reasons = [], []
    for spec in feed.sources:
        http_spec = spec.get(spec.get("type")) or {}
        if spec.get("type") != "http":
            reasons.append(f"a {spec.get('type')} stream")
        elif "authorization" in http_spec:
            reasons.append("the source needs a key")
        elif "archive" in http_spec:
            reasons.append("an archive member")
        else:
            url = http_spec.get("url", "")
            if url not in cache:
                accept = (http_spec.get("headers") or {}).get("Accept", "*/*")
                r = http.fetch(url, accept, limit=64 << 20, timeout=90)
                if r.status != 200:
                    cache[url] = f"the source answered HTTP {r.status}: {url}"
                elif r.body.startswith(b"PK\x03\x04"):
                    cache[url] = "a zip archive"
                else:
                    cache[url] = ("body", r.body.decode("utf-8", "replace").lower())
            if isinstance(cache[url], str):
                reasons.append(cache[url])
            else:
                bodies.append(cache[url][1])
    if not bodies:
        add(f"source.{typ}", "SKIP", "not probed", "; ".join(sorted(set(reasons))) or "no DataSource")
        return
    sample = rng.sample(items, min(5, len(items)))
    hits = sum(1 for e in sample if any(tok in body for body in bodies for tok in source_tokens(e)))
    ok = bool(sample) and hits >= math.ceil(len(sample) * 0.6)
    # An entity may come from a source that was not probed: only a full probe can fail.
    verdict = "PASS" if ok else ("SKIP" if reasons else "FAIL")
    detail = "" if ok else ", ".join(str(e.get("id")) for e in sample[:3])
    if reasons and not ok:
        detail = f"not probed: {'; '.join(sorted(set(reasons)))}; missing: {detail}"
    add(f"source.{typ}", verdict, f"{hits}/{len(sample)} entities found in the source", detail)


def _schema(http, base, index_r, index, add):
    if index_r.status != 200 or not index.get("models"):
        add("schema.index", "FAIL", f"HTTP {index_r.status}, {len(index.get('models', []))} models")
        return
    add("schema.index", "PASS", f"{len(index['models'])} models")
    bad, n = [], 0
    for model in index["models"]:
        for artifact, meta in (model.get("artifacts") or {}).items():
            n += 1
            r = http.fetch(f"{base}schema/v{model.get('version', 1)}/{artifact}")
            if r.status != 200:
                bad.append(f"{artifact} HTTP {r.status}")
            elif meta.get("sha256") and hashlib.sha256(r.body).hexdigest() != meta["sha256"]:
                bad.append(f"{artifact} sha256 differs")
            elif not parses(artifact, r.body):
                bad.append(f"{artifact} does not parse")
    add("schema.artifacts", "FAIL" if bad or not n else "PASS", f"{n - len(bad)}/{n} answer and parse", "; ".join(bad))


def _ogc_landing(http, base, add) -> set:
    collections: set = set()
    for path in ("ogc/features/", "ogc/features/conformance", "ogc/features/collections"):
        r = http.fetch(base + path, "application/json")
        add(f"ogc.{path.rstrip('/').rsplit('/', 1)[-1]}", "PASS" if r.status == 200 else "FAIL", f"HTTP {r.status}")
        if r.status == 200 and path.endswith("collections"):
            collections = {c.get("id") for c in json.loads(r.body).get("collections", [])}
    return collections


def _websocket(http, base, types, live, seconds) -> list[tuple]:
    if not types:
        return [("ws.subscribe", "SKIP", "no type")]
    lines = []
    url = base + "ws"
    probe_type = live[0] if live else types[0]
    started = dt.datetime.now(dt.timezone.utc)
    upgrade, status, notes = http.ws(url, {"entities": [{"type": probe_type}]}, seconds if live else 0)
    lines.append(("ws.subscribe", "PASS" if (upgrade, status) == (101, 201) else "FAIL",
                  f"upgrade {upgrade}, x-status {status}", probe_type))
    if not live:
        lines.append(("ws.notify", "SKIP", "no live type", "no type is written within 10 min"))
    elif notes:
        lines.append(("ws.notify", "PASS", f"{notes} notifications in {seconds:.0f}s", probe_type))
    elif status == 201:
        items, _, error = scan(http, base, probe_type)
        newest = max((t for t in map(newest_write, items) if t), default=None)
        if not error and newest and newest >= started:
            lines.append(("ws.notify", "FAIL", f"0 notifications in {seconds:.0f}s",
                          f"{probe_type} changed at {newest.isoformat(timespec='seconds')}"))
        else:
            lines.append(("ws.notify", "SKIP", f"0 notifications in {seconds:.0f}s", f"{probe_type} did not change"))
    upgrade, status, _ = http.ws(url, {"entities": [{"type": probe_type}], "q": "((("}, 0)
    lines.append(("ws.invalid_q", "PASS" if status == 400 else "FAIL", f"upgrade {upgrade}, x-status {status}"))
    return lines


def ogc_walk(http, page) -> int | None:
    """numberMatched is optional (OGC 17-069r4 7.15.6): count the features over the next links."""
    n = 0
    while True:
        n += len(page.get("features", []))
        nxt = next((link["href"] for link in page.get("links", []) if link.get("rel") == "next"), None)
        if not nxt or n >= SCAN_CAP:
            return n
        r = http.fetch(nxt, "application/geo+json")
        if r.status != 200:
            return None
        page = json.loads(r.body)


def hours(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h" if seconds < 86400 * 2 else f"{seconds / 86400:.1f}d"

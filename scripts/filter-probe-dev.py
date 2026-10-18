#!/usr/bin/env python3
"""T-4467: does filtering work on the live Endpoints, in every standard each one serves?

For every public Endpoint in dev's DCAT feed: read its first type and one entity, pick one
attribute (a number gives `attr >= v`, else a short string gives `attr == "v"`) and a box of
0.1 degrees around the entity's point, then ask the same question in NGSI-LD (`q` + georel),
OData (`$filter`), OGC API Features (CQL2 `filter` + `bbox`) and STA (`$filter` on `name`, on the
Things); the Endpoint's one file takes no filter (`file.geojson` was removed, T-4597). A
standard's answer is `ok` when every entity it returns satisfies the filter and its total equals
NGSI-LD's (and its ids equal NGSI-LD's when both fit on one page of 100); else `mismatch`; `not served` when the feed lists no such
distribution; `error` when it answered 5xx, timed out or was not JSON. One malformed filter per
standard must answer 400.

Data sources: the Endpoints and their distributions are `dcat:Dataset` nodes of
`{base}/catalog.jsonld` (`dcat:distribution[].dcat:accessURL`); the type is `typeList[0]` of
`/ngsi-ld/v1/types`; totals are the `NGSILD-Results-Count` header, `@odata.count`,
`numberMatched` and `@iot.count`; every value checked is
read from the answers. Nothing is typed.

Read-only and anonymous (public Endpoints need no token, so none is ever held); sequential, one
request every `--pace` seconds (default 1), never concurrent: dev is one node.

    python3 scripts/filter-probe-dev.py [--base https://dev.joinedcontext.com] [--only SLUG] [--json out.json]
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAGE = 100
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
STANDARDS = ("ngsi-ld", "odata", "ogc", "sta")
MARKERS = {
    "ngsi-ld": "/ngsi-ld/v1/",
    "odata": "/odata/v4/",
    "ogc": "/ogc/features/",
    "sta": "/sta/v1.1/",
}


class Http:
    def __init__(self, pace):
        self.pace, self.last = pace, 0.0

    def get(self, url):
        """(status, headers, parsed JSON or None); never raises on an HTTP or network error."""
        time.sleep(max(0.0, self.last + self.pace - time.monotonic()))
        self.last = time.monotonic()
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as r:
                status, headers, body = r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            status, headers, body = e.code, dict(e.headers), e.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, {}, None
        try:
            return status, headers, json.loads(body)
        except ValueError:
            return status, headers, None


def endpoints(feed):
    """[(endpoint URL, {standard: access URL})] of the feed's Endpoints, in feed order."""
    out = []
    for node in feed.get("@included", []):
        if node.get("@type") != "dcat:Dataset" or not node.get("dcat:distribution"):
            continue
        urls = [d.get("dcat:accessURL", "") for d in node["dcat:distribution"]]
        served = {s: next((u for u in urls if m in u), None) for s, m in MARKERS.items()}
        out.append((node["@id"], {s: u for s, u in served.items() if u}))
    return out


def pick(entity):
    """(attribute, op, value) the probe filters on: a number first, else a short string."""
    plain = sorted(k for k in entity if NAME.match(k) and k not in ("id", "type", "location"))
    for k in plain:
        v = entity[k]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return k, ">=", v
    for k in plain:
        v = entity[k]
        # A date-time is a typed literal in each language, not a string: not the probe's case.
        if isinstance(v, str) and 0 < len(v) <= 60 and not STAMP.match(v) and not set(v) & set("\"'\\;|()&=<>~"):
            return k, "==", v
    return None


def box(entity):
    """[w, s, e, n] of 0.1 degrees around the entity's point, or None."""
    geo = entity.get("location")
    if not isinstance(geo, dict) or geo.get("type") != "Point":
        return None
    lon, lat = geo["coordinates"][:2]
    return [round(lon - 0.05, 6), round(lat - 0.05, 6), round(lon + 0.05, 6), round(lat + 0.05, 6)]


def satisfies(value, op, wanted):
    if op == "==":
        return value == wanted
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= wanted


def inside(geometry, bbox):
    """Whether a point lies in the box. A feature without a geometry matches any box in OGC
    (`/req/core/fc-bbox-response` C), and a non-point is not checked."""
    if not bbox or not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return True
    lon, lat = geometry["coordinates"][:2]
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def literal(op, value, quote):
    if op == ">=":
        return repr(value)
    return quote + value.replace(quote, quote * 2) + quote


def ngsi_params(t, f, bbox):
    attr, op, v = f
    q = f"{attr}>={v!r}" if op == ">=" else f'{attr}=="{v}"'
    params = {"type": t, "q": q, "options": "keyValues"}
    if bbox:
        w, s, e, n = bbox
        params.update(georel="intersects", geometry="Polygon",
                      coordinates=json.dumps([[[w, s], [e, s], [e, n], [w, n], [w, s]]], separators=(",", ":")))
    return params


def urls(served, t, f, bbox):
    """{standard: the filtered request} for every standard the Endpoint serves."""
    attr, op, v = f
    enc = lambda p: urllib.parse.urlencode(p, quote_via=urllib.parse.quote)
    n = ngsi_params(t, f, bbox)
    out = {"ngsi-ld": f"{served['ngsi-ld']}entities?{enc({**n, 'count': 'true', 'limit': PAGE})}"}
    if "odata" in served:
        filt = f"{attr} {'ge' if op == '>=' else 'eq'} {literal(op, v, chr(39))}"
        out["odata"] = f"{served['odata']}{t}?{enc({'$filter': filt, '$count': 'true', '$top': PAGE})}"
    if "ogc" in served:
        p = {"filter": f"{attr} {'>=' if op == '>=' else '='} {literal(op, v, chr(39))}",
             "filter-lang": "cql2-text", "limit": PAGE}
        if bbox:
            p["bbox"] = ",".join(map(str, bbox))
        out["ogc"] = f"{served['ogc']}collections/{t}/items?{enc(p)}"
    return out


def rows(standard, status, headers, body):
    """(total, [(id, value-holder, geometry)]) of one answer, or an error string."""
    if status == 0:
        return "no answer"
    if status != 200 or body is None:
        return f"HTTP {status}"
    if standard == "ngsi-ld":
        total = headers.get("NGSILD-Results-Count") or headers.get("Ngsild-Results-Count")
        return int(total) if total else None, [(e.get("id"), e, e.get("location")) for e in body]
    if standard == "odata":
        return body.get("@odata.count"), [(e.get("id"), e, None) for e in body.get("value", [])]
    if standard == "sta":
        return body.get("@iot.count"), [(e.get("properties", {}).get("id") or e.get("@iot.id"), e, None)
                                        for e in body.get("value", [])]
    features = body.get("features", [])
    return body.get("numberMatched"), [(x.get("id"), x.get("properties") or {}, x.get("geometry")) for x in features]


def judge(reference, answer, f, bbox):
    """`ok` or `mismatch: why` of one standard's answer against NGSI-LD's (both from `rows`)."""
    if isinstance(answer, str):
        return f"error: {answer}"
    attr, op, v = f
    total, found = answer
    bad = [i for i, holder, geo in found if not satisfies(holder.get(attr), op, v) or not inside(geo, bbox)]
    if bad:
        return f"mismatch: {len(bad)} of {len(found)} returned fail the filter, e.g. {bad[0]}"
    ref_total, ref_found = reference
    if total != ref_total:
        return f"mismatch: total {total} vs NGSI-LD {ref_total}"
    if ref_total is not None and ref_total <= PAGE and {i for i, _, _ in found} != {i for i, _, _ in ref_found}:
        return "mismatch: other ids than NGSI-LD"
    return "ok"


def malformed(http, served, t):
    """{standard: verdict} of one broken filter per standard: anything but 400 is a mismatch."""
    asks = {"ngsi-ld": f"{served['ngsi-ld']}entities?type={t}&q=a%3E%3E%3E"}
    if "odata" in served:
        asks["odata"] = f"{served['odata']}{t}?$filter=a%20ge"
    if "ogc" in served:
        asks["ogc"] = f"{served['ogc']}collections/{t}/items?filter=a%20%3E%3D&filter-lang=cql2-text"
    out = {}
    for s, u in asks.items():
        status = http.get(u)[0]
        out[s] = "ok" if status == 400 else f"mismatch: HTTP {status}, not 400"
    return out


def probe(http, ep, served):
    """{standard: verdict} of one Endpoint."""
    cells = {s: "not served" for s in STANDARDS if s not in served}
    status, _, body = http.get(f"{served['ngsi-ld']}types")
    types = (body or {}).get("typeList") if isinstance(body, dict) else None
    if status != 200:
        return {**cells, **{s: f"error: no types (HTTP {status})" for s in served}}
    if not types:
        return {**cells, **{s: "skipped: the Endpoint holds no type" for s in served}}
    t = types[0]
    status, _, body = http.get(f"{served['ngsi-ld']}entities?type={urllib.parse.quote(t)}&limit=1&options=keyValues")
    if status != 200 or not body:
        return {**cells, **{s: f"skipped: no {t} entity (HTTP {status})" for s in served}}
    entity = body[0]
    f, bbox = pick(entity), box(entity)
    if not f:
        return {**cells, **{s: f"skipped: {t} has no plain attribute" for s in served}}
    asks = urls(served, t, f, bbox)
    reference = rows("ngsi-ld", *http.get(asks["ngsi-ld"]))
    if isinstance(reference, str):
        return {**cells, **{s: f"error: NGSI-LD {reference}" for s in served}}
    cells["ngsi-ld"] = judge(reference, reference, f, bbox)
    # OData here has no geo filter: its reference is the same q without the box.
    plain = reference
    if bbox and "odata" in asks:
        plain = rows("ngsi-ld", *http.get(urls(served, t, f, None)["ngsi-ld"]))
        if isinstance(plain, str):
            cells["odata"] = f"error: NGSI-LD {plain}"
            del asks["odata"]
    # OGC's bbox also keeps the Features without a geometry: NGSI-LD's georel never does, so
    # OGC is held to the boxed answer plus the same q over the entities with no location.
    boxed = reference
    if bbox and "ogc" in asks:
        attr, op, v = f
        q = (f"{attr}>={v!r}" if op == ">=" else f'{attr}=="{v}"') + ";!location"
        enc = urllib.parse.urlencode({"type": t, "q": q, "options": "keyValues", "count": "true", "limit": PAGE},
                                     quote_via=urllib.parse.quote)
        unlocated = rows("ngsi-ld", *http.get(f"{served['ngsi-ld']}entities?{enc}"))
        if isinstance(unlocated, str):
            cells["ogc"] = f"error: NGSI-LD {unlocated}"
            del asks["ogc"]
        else:
            boxed = (None if reference[0] is None or unlocated[0] is None else reference[0] + unlocated[0],
                     reference[1] + unlocated[1])
    for s in ("odata", "ogc"):
        if s not in asks:
            continue
        if s == "ogc":
            cells[s] = judge(boxed, rows(s, *http.get(asks[s])), f, bbox)
            continue
        cells[s] = judge(plain, rows(s, *http.get(asks[s])), f, None)
    if "sta" in served:
        cells["sta"] = sta(http, served, t, entity)
    return cells


def sta(http, served, t, entity):
    """STA maps entities to Things: filter Things on the entity's `name`, compare to NGSI-LD."""
    name = entity.get("name")
    if not isinstance(name, str) or set(name) & set("'\"\\"):
        return "skipped: no plain name"
    f = ("name", "==", name)
    reference = rows("ngsi-ld", *http.get(urls(served, t, f, None)["ngsi-ld"]))
    filt = urllib.parse.quote(f"name eq '{name}'")
    answer = rows("sta", *http.get(f"{served['sta']}Things?$filter={filt}&$count=true&$top={PAGE}"))
    if isinstance(answer, str):
        return f"error: {answer}"
    if isinstance(reference, str):
        return f"error: NGSI-LD {reference}"
    bad = [i for i, e, _ in answer[1] if e.get("name") != name]
    if bad:
        return f"mismatch: {len(bad)} Things fail name eq"
    return "ok" if answer[0] == reference[0] else f"mismatch: total {answer[0]} vs NGSI-LD {reference[0]}"


def table(results):
    head = "| Endpoint | " + " | ".join(STANDARDS) + " |\n|---|" + "---|" * len(STANDARDS)
    lines = [f"| {ep.rsplit('/', 1)[-1]} | " + " | ".join(cells[s] for s in STANDARDS) + " |"
             for ep, cells in results]
    return "\n".join([head, *lines])


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--base", default="https://api.dev.joinedcontext.com")
    a.add_argument("--pace", type=float, default=1.0)
    a.add_argument("--only")
    a.add_argument("--json")
    args = a.parse_args(argv)
    http = Http(max(args.pace, 0.3))
    status, _, feed = http.get(f"{args.base}/catalog.jsonld")
    if status != 200 or not isinstance(feed, dict):
        print(f"catalog.jsonld: HTTP {status}", file=sys.stderr)
        return 2
    eps = [(ep, s) for ep, s in endpoints(feed) if "ngsi-ld" in s and (not args.only or ep.endswith(args.only))]
    results, down = [], 0
    for ep, served in eps:
        cells = probe(http, ep, served)
        if cells["ngsi-ld"].startswith("error: no types (HTTP 5"):
            # A broker restarting answers 5xx for a minute: wait once, then ask again.
            time.sleep(60)
            cells = probe(http, ep, served)
        down = down + 1 if cells["ngsi-ld"].startswith("error") else 0
        results.append((ep, cells))
        print(ep.rsplit("/", 1)[-1], cells, file=sys.stderr, flush=True)
        if down >= 3:
            # Dev is down, not this Endpoint: stop asking rather than pile requests on it.
            print(f"dev answers 5xx on {down} Endpoints in a row: stopped", file=sys.stderr)
            break
    if down >= 3:
        print(f"Stopped after {len(results)} of {len(eps)} Endpoints: dev answered 5xx three times in a row.")
        print(table(results))
        return 2
    if eps:
        bad = malformed(http, eps[0][1], urllib.parse.quote(probe_type(http, eps[0][1])))
        print("\nMalformed filter answers 400: " + ", ".join(f"{s} {v}" for s, v in bad.items()))
    print(table(results))
    if args.json:
        with open(args.json, "w") as out:
            json.dump(results, out, indent=1)
    return 1 if any(v.startswith(("mismatch", "error")) for _, c in results for v in c.values()) else 0


def probe_type(http, served):
    _, _, body = http.get(f"{served['ngsi-ld']}types")
    return ((body or {}).get("typeList") or ["x"])[0]


if __name__ == "__main__":
    sys.exit(main())

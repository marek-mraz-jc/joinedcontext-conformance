#!/usr/bin/env python3
"""T-5130: do the live surfaces of a public Endpoint tell the same story, and does the catalogue?

For each Endpoint and each type it holds, two cases: the whole type, and one attribute filter
(`pick` of scripts/filter-probe-dev.py over the type's first entity: `attr >= number` or
`attr == "string"`). Each case asks NGSI-LD for its total and its first 20 ids, then asks OData,
OGC API Features and the CSV file for the same filter's total and for those 20 ids under the same
filter. A difference is a total other than NGSI-LD's, a sampled id a surface does not return, or a
plain value (string, number, boolean) of a sampled row that a surface gives otherwise or not at all.
SensorThings maps every entity to a Thing: its Thing total must equal the Endpoint's entity total,
and each of 20 sampled Things must carry the entity's string properties unchanged.
The catalogue: each CKAN dataset that names the Endpoint lists its types as `NGSI-LD API (Type)`
resources; every such type must hold an entity. (A DataStore table states no count of a type:
several tables feed one type, and T-5060 removes the tables.)

Data sources: surfaces from the DCAT feed `{api}/catalog.jsonld` (`dcat:accessURL`), types from
`/ngsi-ld/v1/types`, totals from `NGSILD-Results-Count`, `@odata.count`, `numberMatched`,
`@iot.count` and the CSV row count, the catalogue from CKAN `package_search`. Nothing is typed.
A CSV past the Endpoint's `fileLimits` answers 413 (docs API/02 §6): its total is listed as not
compared, and its 20 sampled rows are still compared.

Read-only and anonymous, one request every `--pace` seconds (default 1), never concurrent: the
edge allows 600 a minute per address.

    consistency.py [--api URL] [--catalogue URL] [--json out.json] ENDPOINT_ID [...]
    consistency.py --self-test

Prints Markdown, exits 1 on any difference, 2 when prod did not answer.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "filter_probe",
    Path(__file__).resolve().parents[2] / "scripts" / "filter-probe-dev.py",
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

SAMPLE = 20
NOTE = "not compared: "
# An Article body in one CSV cell passes csv's 128 KiB default; the answer itself is bounded.
csv.field_size_limit(64 * 1024 * 1024)
MARKERS = {**probe.MARKERS, "csv": "/file.csv"}
UA = "joinedcontext-prod-nightly consistency (T-5130)"
enc = lambda p: urllib.parse.urlencode(p, quote_via=urllib.parse.quote)


def patient(fetch, url, tries=3):
    """fetch(url), asked again after a 503's Retry-After (the gateway's two download slots and
    its shedding answer that way, T-4782); a 503 that stays is still a difference."""
    for _ in range(tries - 1):
        answer = fetch(url)
        wait = answer[1].get("Retry-After") if isinstance(answer[1], dict) else None
        if answer[0] != 503 or not str(wait or "").isdigit():
            return answer
        time.sleep(min(int(wait), 30))
    return fetch(url)


class Http(probe.Http):
    def get(self, url):
        return patient(super().get, url)

    def text(self, url):
        status, _, body = patient(self._text, url)
        return status, body

    def _text(self, url):
        """(status, headers, body text); never raises on an HTTP or network error."""
        time.sleep(max(0.0, self.last + self.pace - time.monotonic()))
        self.last = time.monotonic()
        request = urllib.request.Request(
            url, headers={"Accept": "text/csv", "User-Agent": UA}
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as r:
                return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), ""
        except (urllib.error.URLError, TimeoutError, OSError):
            return 0, {}, ""


def plain(row):
    """{attribute: value} of the row's strings, numbers and booleans, id and type aside."""
    return {
        k: v
        for k, v in row.items()
        if k not in ("id", "type") and isinstance(v, (str, int, float, bool))
    }


def same(want, got):
    """Whether a surface's value is the NGSI-LD value; CSV gives every value as text."""
    if got is None:
        return False
    if isinstance(want, bool):
        return str(got).lower() == str(want).lower()
    if isinstance(want, (int, float)):
        try:
            return float(got) == float(want)
        except (TypeError, ValueError):
            return False
    return str(got) == want


def quoted(ids):
    return ",".join("'" + i.replace("'", "''") + "'" for i in ids)


def q_of(f):
    if not f:
        return None
    attr, op, v = f
    return f"{attr}>={v!r}" if op == ">=" else f'{attr}=="{v}"'


def surface_filter(f, op_names):
    """The case's filter in OData (`ge`/`eq`) or CQL2 (`>=`/`=`), or None for the whole type."""
    if not f:
        return None
    attr, op, v = f
    return f"{attr} {op_names[op]} {probe.literal(op, v, chr(39))}"


def asks(served, t, f, ids):
    """{surface: (count request, sample request)} of one case."""
    q = q_of(f)
    base = {"type": t, **({"q": q} if q else {})}
    out = {
        "ngsi-ld": (
            f"{served['ngsi-ld']}entities?{enc({**base, 'count': 'true', 'limit': SAMPLE, 'options': 'keyValues'})}",
            None,
        )
    }
    if "odata" in served:
        filt = surface_filter(f, {">=": "ge", "==": "eq"})
        sample = f"id in ({quoted(ids)})" + (f" and ({filt})" if filt else "")
        root = f"{served['odata']}{urllib.parse.quote(t)}?"
        out["odata"] = (
            root
            + enc({**({"$filter": filt} if filt else {}), "$count": "true", "$top": 1}),
            root + enc({"$filter": sample, "$top": SAMPLE}),
        )
    if "ogc" in served:
        filt = surface_filter(f, {">=": ">=", "==": "="})
        sample = f"id IN ({quoted(ids)})" + (f" AND ({filt})" if filt else "")
        root = f"{served['ogc']}collections/{urllib.parse.quote(t)}/items?"
        lang = {"filter-lang": "cql2-text"}
        out["ogc"] = (
            root + enc({**({"filter": filt, **lang} if filt else {}), "limit": 1}),
            root + enc({"filter": sample, **lang, "limit": SAMPLE}),
        )
    if "csv" in served:
        out["csv"] = (
            f"{served['csv']}?{enc(base)}",
            f"{served['csv']}?{enc({**base, 'id': ','.join(ids)})}",
        )
    return out


def parse(surface, answer):
    """(total, {id: plain values}) of a JSON answer, or an error string."""
    status, headers, body = answer
    if status != 200 or body is None:
        return "no answer" if status == 0 else f"HTTP {status}"
    if surface == "ngsi-ld":
        total = {k.lower(): v for k, v in headers.items()}.get("ngsild-results-count")
        return (int(total) if total else None), {e["id"]: plain(e) for e in body}
    if surface == "odata":
        return body.get("@odata.count"), {
            e["id"]: plain(e) for e in body.get("value", [])
        }
    if surface == "sta":
        return body.get("@iot.count"), {}
    return body.get("numberMatched"), {
        x["id"]: plain(x.get("properties") or {}) for x in body.get("features", [])
    }


def parse_csv(answer):
    """(row count, {id: {attribute: cell}}) of a CSV answer; `a.value` is attribute a."""
    status, text = answer
    if status != 200:
        return "no answer" if status == 0 else f"HTTP {status}"
    try:
        rows = list(csv.DictReader(io.StringIO(text), strict=True))
    except csv.Error as e:
        return f"not CSV ({e})"
    return len(rows), {
        r["id"]: {
            k[:-6]: v for k, v in r.items() if k and k.endswith(".value") and v != ""
        }
        for r in rows
        if r.get("id")
    }


def compare(where, ref, surface, total, rows):
    """Differences of one surface's (total, rows) against NGSI-LD's (total, rows)."""
    ref_total, ref_rows = ref
    out = []
    if total != ref_total:
        out.append(f"{where}: {surface} total {total}, NGSI-LD {ref_total}")
    if rows is None:
        return out
    missing = [i for i in ref_rows if i not in rows]
    if missing:
        out.append(
            f"{where}: {surface} misses {len(missing)} of {len(ref_rows)} sampled ids, e.g. {missing[0]}"
        )
    for i, want in ref_rows.items():
        got = rows.get(i)
        if got is None:
            continue
        bad = [a for a, v in want.items() if not same(v, got.get(a))]
        if bad:
            out.append(
                f"{where}: {surface} row {i} differs in {', '.join(sorted(bad))} "
                f"({bad[0]}: NGSI-LD {want[bad[0]]!r}, {surface} {got.get(bad[0])!r})"
            )
            break  # one row names the defect; every differing row would bury the report
    return out


def case(http, served, t, f, where):
    """Differences of one (type, filter) case over every surface but STA, and NGSI-LD's
    (total, sampled rows)."""
    count_url, _ = asks(served, t, f, [])["ngsi-ld"]
    ref = parse("ngsi-ld", http.get(count_url))
    if isinstance(ref, str):
        return [f"{where}: NGSI-LD {ref}"], (0, {})
    out = []
    for surface, (count, sample) in asks(served, t, f, list(ref[1])).items():
        if surface == "ngsi-ld":
            continue
        if surface == "csv":
            whole = parse_csv(http.text(count))
            if whole == "HTTP 413":
                out.append(
                    f"{NOTE}{where}: csv is past the Endpoint's file limit (413)"
                )
                whole = (ref[0], {})
            got = (
                whole
                if isinstance(whole, str) or not ref[1]
                else parse_csv(http.text(sample))
            )
        else:
            whole = parse(surface, http.get(count))
            got = (
                whole
                if isinstance(whole, str) or not ref[1]
                else parse(surface, http.get(sample))
            )
        if isinstance(whole, str) or isinstance(got, str):
            out.append(f"{where}: {surface} {whole if isinstance(whole, str) else got}")
            continue
        out += compare(where, ref, surface, whole[0], got[1] if ref[1] else None)
    return out, ref


def sta(http, served, types, sampled, ep):
    """Differences of the STA Things against the Endpoint's entities."""
    url = f"{served['ngsi-ld']}entities?{enc({'type': ','.join(types), 'count': 'true', 'limit': 1})}"
    ref = parse("ngsi-ld", http.get(url))
    things = parse("sta", http.get(f"{served['sta']}Things?$count=true&$top=1"))
    if isinstance(ref, str) or isinstance(things, str):
        return [f"{ep} STA: {'NGSI-LD ' + ref if isinstance(ref, str) else things}"]
    out = (
        []
        if things[0] == ref[0]
        else [f"{ep} STA: {things[0]} Things, {ref[0]} entities"]
    )
    for i, want in sampled[:SAMPLE]:
        status, _, body = http.get(
            f"{served['sta']}Things('{urllib.parse.quote(i, safe=':')}')"
        )
        if status != 200 or not isinstance(body, dict):
            out.append(f"{ep} STA: Thing {i} answers HTTP {status}")
            continue
        props = body.get("properties") or {}
        bad = sorted(
            a
            for a, v in want.items()
            if isinstance(v, str) and a in props and props[a] != v
        )
        if bad:
            out.append(f"{ep} STA: Thing {i} differs in {', '.join(bad)}")
    return out


def catalogue_diffs(packages, endpoint_url, counts):
    """Differences of the CKAN datasets naming the Endpoint against its type counts."""
    out = []
    for p in packages:
        extras = {e["key"]: e["value"] for e in p.get("extras", [])}
        if extras.get("endpoint", "").rstrip("/") != endpoint_url.rstrip("/"):
            continue
        types = sorted(
            {
                r["name"][len("NGSI-LD API (") : -1]
                for r in p.get("resources", [])
                if r.get("name", "").startswith("NGSI-LD API (")
                and r["name"].endswith(")")
            }
        )
        for t in types:
            if not counts.get(t):
                out.append(
                    f"catalogue {p['name']}: lists {t}, the Endpoint holds {counts.get(t, 0)}"
                )
    return out


def packages_of(http, catalogue):
    """Every CKAN dataset, 1000 a page; None when CKAN does not answer."""
    out, start = [], 0
    while True:
        status, _, body = http.get(
            f"{catalogue}/api/3/action/package_search?{enc({'rows': 1000, 'start': start})}"
        )
        if status != 200 or not isinstance(body, dict):
            return None
        page = body["result"]["results"]
        out += page
        start += len(page)
        if not page or start >= body["result"]["count"]:
            return out


def endpoint(http, ep, served, packages, catalogue):
    """Differences of one Endpoint, and its per-type NGSI-LD counts."""
    status, _, body = http.get(f"{served['ngsi-ld']}types")
    if status != 200 or not isinstance(body, dict):
        return [f"{ep}: NGSI-LD types answer HTTP {status}"], {}
    types = body.get("typeList") or []
    out, counts, sampled = [], {}, []
    for t in types:
        diffs, (total, rows) = case(http, served, t, None, f"{ep} {t}")
        out += diffs
        counts[t] = total or 0
        print(
            f"{ep} {t}: {counts[t]} entities, {len(out)} differences so far",
            file=sys.stderr,
            flush=True,
        )
        sampled += list(rows.items())[: max(1, SAMPLE // len(types))]
        f = probe.pick(next(iter(rows.values()))) if rows else None
        if f:
            out += case(http, served, t, f, f"{ep} {t} [{q_of(f)}]")[0]
    if "sta" in served and types:
        out += sta(http, served, types, sampled, ep)
    if packages is None:
        out.append(f"{ep}: CKAN package_search did not answer")
    else:
        out += catalogue_diffs(
            packages, served["ngsi-ld"].rsplit("/ngsi-ld/", 1)[0], counts
        )
    return out, counts


def run(http, api, catalogue, ids):
    """(differences, {endpoint: counts}); raises SystemExit(2) when the DCAT feed is down."""
    status, _, feed = http.get(f"{api}/catalog.jsonld")
    if status != 200 or not isinstance(feed, dict):
        raise SystemExit(f"catalog.jsonld: HTTP {status}")
    feed_eps = {}
    for node in feed.get("@included", []):
        if node.get("@type") == "dcat:Dataset" and node.get("dcat:distribution"):
            urls = [d.get("dcat:accessURL", "") for d in node["dcat:distribution"]]
            feed_eps[node["@id"].rstrip("/").rsplit("/", 1)[-1]] = {
                s: next((u for u in urls if m in u), None) for s, m in MARKERS.items()
            }
    packages = packages_of(http, catalogue)
    diffs, counts = [], {}
    for ep in ids:
        served = {s: u for s, u in feed_eps.get(ep, {}).items() if u}
        if "ngsi-ld" not in served:
            diffs.append(f"{ep}: the DCAT feed lists no NGSI-LD distribution")
            continue
        d, counts[ep] = endpoint(http, ep, served, packages, catalogue)
        diffs += d
    return diffs, counts


def report(diffs, counts):
    lines = ["## Cross-surface consistency (T-5130)"]
    for ep, c in counts.items():
        lines.append(f"- endpoint {ep}: {len(c)} types, {sum(c.values())} entities")
    real = [d for d in diffs if not d.startswith(NOTE)]
    lines.append(f"- differences: {len(real)}")
    lines += [f"  - {d}" for d in real]
    lines += [f"- {d}" for d in diffs if d.startswith(NOTE)]
    return "\n".join(lines)


class Fake:
    """Recorded answers by URL prefix (the longest matching prefix wins), for the self-test."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def _find(self, url):
        self.asked.append(url)
        keys = [k for k in self.answers if url.startswith(k)]
        return self.answers[max(keys, key=len)] if keys else (404, {}, None)

    def get(self, url):
        return self._find(url)

    def text(self, url):
        status, _, body = self._find(url)
        return status, body or ""


def self_test():
    busy = iter([(503, {"Retry-After": "0"}, None), (200, {}, "ok")])
    assert patient(lambda _: next(busy), "u") == (200, {}, "ok")
    assert patient(lambda _: (503, {"Retry-After": "0"}, None), "u")[0] == 503
    assert patient(lambda _: (503, {}, None), "u")[0] == 503
    a, b = "urn:ngsi-ld:Garden:x:1", "urn:ngsi-ld:Garden:x:2"
    ref = (2, {a: {"vrstva": "zeleň", "typ": 2}, b: {"vrstva": "db", "typ": 0}})
    assert (
        compare(
            "w",
            ref,
            "odata",
            2,
            {a: {"vrstva": "zeleň", "typ": 2}, b: {"vrstva": "db", "typ": 0}},
        )
        == []
    )
    assert (
        compare(
            "w",
            ref,
            "csv",
            2,
            {a: {"vrstva": "zeleň", "typ": "2"}, b: {"vrstva": "db", "typ": "0.0"}},
        )
        == []
    )
    assert compare("w", ref, "ogc", 3, None) == ["w: ogc total 3, NGSI-LD 2"]
    assert compare("w", ref, "ogc", 2, {a: ref[1][a]}) == [
        f"w: ogc misses 1 of 2 sampled ids, e.g. {b}"
    ]
    [d] = compare(
        "w", ref, "csv", 2, {a: {"vrstva": "zelen", "typ": "2"}, b: ref[1][b]}
    )
    assert d.startswith(f"w: csv row {a} differs in vrstva"), d
    [d] = compare("w", ref, "odata", 2, {a: {"vrstva": "zeleň"}, b: ref[1][b]})
    assert "differs in typ" in d and "odata None" in d, d
    assert (
        same(True, "true")
        and same(10027952.0, "10027952")
        and not same(1, "x")
        and not same("a", None)
    )
    assert parse_csv((200, 'id\n"a"b\n')).startswith("not CSV")
    assert parse_csv((200, 'id,type,a.value,b.value[0]\nu1,T,"x, y",1\nu2,T,,2\n')) == (
        2,
        {"u1": {"a": "x, y"}, "u2": {}},
    )
    assert parse(
        "ngsi-ld",
        (
            200,
            {"NGSILD-Results-Count": "3"},
            [{"id": "u", "type": "T", "n": 1, "o": {}}],
        ),
    ) == (3, {"u": {"n": 1}})
    assert parse("ogc", (503, {}, None)) == "HTTP 503"
    # One filter, the same everywhere: the sample asks carry the case filter and the sampled ids.
    served = {"ngsi-ld": "N/", "odata": "O/", "ogc": "G/", "csv": "C"}
    u = asks(served, "T", ("typ", ">=", 2), [a])
    assert urllib.parse.unquote(u["odata"][1]).endswith(
        f"$filter=id in ('{a}') and (typ ge 2)&$top=20"
    )
    assert "filter=id IN ('" + a + "') AND (typ >= 2)" in urllib.parse.unquote(
        u["ogc"][1]
    )
    assert urllib.parse.unquote(u["csv"][1]) == f"C?type=T&q=typ>=2&id={a}"
    pkg = lambda name, types: {
        "name": name,
        "extras": [{"key": "endpoint", "value": "E/"}],
        "resources": [{"name": f"NGSI-LD API ({t})", "id": "n"} for t in types]
        + [{"name": "tabuľka", "id": "r", "datastore_active": True}],
    }
    pkgs = [
        pkg("bb-a", ["Garden"]),
        pkg("bb-b", ["CouncilMeeting"]),
        pkg("bb-c", ["Garden", "Address"]),
    ]
    assert catalogue_diffs(pkgs, "E", {"Garden": 2, "Address": 3}) == [
        "catalogue bb-b: lists CouncilMeeting, the Endpoint holds 0"
    ]
    # End to end over recorded answers: one Endpoint, one type, CSV one row short.
    e = "https://api/api/endpoint/ep1"
    rows = [
        {"id": a, "type": "Garden", "vrstva": "zeleň", "typ": 2},
        {"id": b, "type": "Garden", "vrstva": "db", "typ": 0},
    ]
    dist = [
        {"dcat:accessURL": f"{e}/{p}"}
        for p in ("ngsi-ld/v1/", "odata/v4/", "ogc/features/", "sta/v1.1/", "file.csv")
    ]
    count = {"NGSILD-Results-Count": "2"}
    fake = Fake(
        {
            "https://api/catalog.jsonld": (
                200,
                {},
                {
                    "@included": [
                        {"@id": e, "@type": "dcat:Dataset", "dcat:distribution": dist}
                    ]
                },
            ),
            "https://ckan/api/3/action/package_search": (
                200,
                {},
                {"result": {"count": 1, "results": [pkg("bb-a", ["Garden"])]}},
            ),
            f"{e}/ngsi-ld/v1/types": (200, {}, {"typeList": ["Garden"]}),
            f"{e}/ngsi-ld/v1/entities": (200, count, rows),
            f"{e}/odata/v4/Garden": (200, {}, {"@odata.count": 2, "value": rows}),
            f"{e}/ogc/features/collections/Garden/items": (
                200,
                {},
                {
                    "numberMatched": 2,
                    "features": [
                        {
                            "id": r["id"],
                            "properties": {k: v for k, v in r.items() if k != "id"},
                        }
                        for r in rows
                    ],
                },
            ),
            f"{e}/file.csv": (
                200,
                {},
                f"id,type,vrstva.value,typ.value\n{a},Garden,zeleň,2\n",
            ),
            f"{e}/sta/v1.1/Things?": (200, {}, {"@iot.count": 2, "value": []}),
            f"{e}/sta/v1.1/Things(": (200, {}, {"properties": {"vrstva": "zeleň"}}),
        }
    )
    diffs, counts = run(fake, "https://api", "https://ckan", ["ep1", "gone"])
    assert counts == {"ep1": {"Garden": 2}}, counts
    assert diffs == [
        "ep1 Garden: csv total 1, NGSI-LD 2",
        f"ep1 Garden: csv misses 1 of 2 sampled ids, e.g. {b}",
        "ep1 Garden [typ>=2]: csv total 1, NGSI-LD 2",
        f"ep1 Garden [typ>=2]: csv misses 1 of 2 sampled ids, e.g. {b}",
        f"ep1 STA: Thing {b} differs in vrstva",
        "gone: the DCAT feed lists no NGSI-LD distribution",
    ], diffs
    # A CSV past the file limit (413) is listed, not counted, and its sample still compared.
    csv_rows = f"id,type,vrstva.value,typ.value\n{a},Garden,zeleň,2\n{b},Garden,db,0\n"
    fake.answers[f"{e}/file.csv?type=Garden"] = (413, {}, None)
    fake.answers[f"{e}/file.csv?type=Garden&id="] = (200, {}, csv_rows)
    served = {"ngsi-ld": f"{e}/ngsi-ld/v1/", "csv": f"{e}/file.csv"}
    assert case(fake, served, "Garden", None, "w")[0] == [
        f"{NOTE}w: csv is past the Endpoint's file limit (413)"
    ]
    assert report([f"{NOTE}x", "y"], {}).splitlines()[1:] == [
        "- differences: 1",
        "  - y",
        f"- {NOTE}x",
    ]
    print("consistency self-test: ok")


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--api", default="https://api.smartcity.joinedcontext.com")
    a.add_argument(
        "--catalogue", default="https://opendata.smartcity.joinedcontext.com"
    )
    a.add_argument("--pace", type=float, default=1.0)
    a.add_argument("--json")
    a.add_argument("--self-test", action="store_true")
    a.add_argument("endpoints", nargs="*")
    args = a.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    try:
        diffs, counts = run(
            Http(max(args.pace, 0.3)),
            args.api.rstrip("/"),
            args.catalogue.rstrip("/"),
            args.endpoints,
        )
    except SystemExit as e:
        print(f"## Cross-surface consistency (T-5130)\n- prod did not answer: {e}")
        return 2
    print(report(diffs, counts))
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"differences": diffs, "counts": counts}, indent=1, ensure_ascii=False
            )
        )
    return 1 if any(not d.startswith(NOTE) for d in diffs) else 0


if __name__ == "__main__":
    sys.exit(main())

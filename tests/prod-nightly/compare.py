#!/usr/bin/env python3
"""Verdicts of the nightly read-only standards run against production (T-4877).

    compare.py ogc RESULTS.xml BASELINE.json     OGC API Features ETS run against Antares's CI baseline
    compare.py geojson URL [URL ...]             RFC 7946 checks of OGC items pages
    compare.py ical URL [URL ...]                RFC 5545 checks of iCalendar feeds
    compare.py --self-test

The ogc mode reads a run with the broker's own parser (dev/ogc-compliance/baseline.py of the
Antares checkout in $ANTARES_DIR, at the version prod runs).
A regression is a test that PASSes in the CI baseline and FAILs on prod. A baseline pass that prod
SKIPs or never runs is listed, not counted: prod's public views hold other data than the CI fixtures. Prints Markdown, exits 1 on a
regression or an invalid document.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.request

UA = {
    "User-Agent": "joinedcontext-prod-nightly (T-4877)",
    "Accept": "application/geo+json, application/json",
}


def broker_parser(kind: str):
    path = os.path.join(
        os.environ.get("ANTARES_DIR", ""), "dev", f"{kind}-compliance", "baseline.py"
    )
    spec = importlib.util.spec_from_file_location(f"{kind}_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse


def diff(
    prod: dict[str, str], baseline: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """(regressions, baseline passes prod did not run, prod passes the baseline lacks)."""
    lost = [t for t, s in baseline.items() if s == "PASS" and prod.get(t) == "FAIL"]
    unrun = [
        t
        for t, s in baseline.items()
        if s == "PASS" and prod.get(t) not in ("PASS", "FAIL")
    ]
    gained = [t for t, s in prod.items() if s == "PASS" and baseline.get(t) != "PASS"]
    return sorted(lost), sorted(unrun), sorted(gained)


def report(name: str, prod: dict[str, str], baseline: dict[str, str]) -> int:
    lost, unrun, gained = diff(prod, baseline)
    counts = {
        s: sum(1 for v in prod.values() if v == s) for s in ("PASS", "FAIL", "SKIP")
    }
    print(
        f"- {name}: {counts['PASS']} pass, {counts['FAIL']} fail, {counts['SKIP']} skip;"
        f" **{len(lost)} regressions** against CI, {len(unrun)} CI passes not run here, {len(gained)} passes CI lacks"
    )
    for t in lost:
        print(f"  - REGRESSION {t}")
    return 1 if lost else 0


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=60
    ) as response:
        return response.read(32 * 1024 * 1024)


GEOMETRIES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


def positions(coords):
    if coords and isinstance(coords[0], (int, float)):
        yield coords
    else:
        for c in coords or []:
            yield from positions(c)


def geometry_errors(g, where: str) -> list[str]:
    if g is None:
        return []
    if g.get("type") not in GEOMETRIES:
        return [f"{where}: geometry type {g.get('type')!r}"]
    if g["type"] == "GeometryCollection":
        return [
            e
            for i, m in enumerate(g.get("geometries", []))
            for e in geometry_errors(m, f"{where}[{i}]")
        ]
    errors = []
    for p in positions(g.get("coordinates")):
        if len(p) < 2 or not all(isinstance(v, (int, float)) for v in p):
            errors.append(f"{where}: position {p!r}")
        elif not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            errors.append(f"{where}: position {p[:2]} outside lon/lat (RFC 7946 4)")
    if g["type"] == "Polygon":
        for ring in g.get("coordinates", []):
            if len(ring) < 4 or ring[0] != ring[-1]:
                errors.append(
                    f"{where}: polygon ring not closed or under 4 positions (RFC 7946 3.1.6)"
                )
    return errors[:5]


def geojson_errors(doc: dict) -> list[str]:
    if doc.get("type") != "FeatureCollection" or not isinstance(
        doc.get("features"), list
    ):
        return ["not a FeatureCollection with a features array (RFC 7946 3.3)"]
    errors = ["a crs member (removed by RFC 7946 4)"] if "crs" in doc else []
    for i, f in enumerate(doc["features"]):
        if f.get("type") != "Feature" or "geometry" not in f or "properties" not in f:
            errors.append(
                f"feature {i}: needs type Feature, geometry and properties (RFC 7946 3.2)"
            )
        else:
            errors += geometry_errors(f["geometry"], f"feature {i}")
        if "bbox" in f and len(f["bbox"]) not in (4, 6):
            errors.append(f"feature {i}: bbox of {len(f['bbox'])} numbers (RFC 7946 5)")
    return errors


def ical_errors(text: str) -> list[str]:
    errors = []
    if "\r\n" not in text or text.replace("\r\n", "").count("\n"):
        errors.append("lines not delimited by CRLF (RFC 5545 3.1)")
    lines = text.replace("\r\n ", "").replace("\r\n\t", "").split("\r\n")
    if any(len(line.encode()) > 75 for line in text.split("\r\n")):
        errors.append("a line longer than 75 octets, not folded (RFC 5545 3.1)")
    if not lines or lines[0] != "BEGIN:VCALENDAR" or "END:VCALENDAR" not in lines:
        return errors + ["not one VCALENDAR (RFC 5545 3.4)"]
    names = [line.split(":", 1)[0].split(";", 1)[0] for line in lines if line]
    for prop in ("VERSION", "PRODID"):
        if prop not in names:
            errors.append(f"VCALENDAR without {prop} (RFC 5545 3.6)")
    event, n = None, 0
    for name in names:
        if name == "BEGIN" and event is None:
            event = set()
        elif name == "END" and event is not None:
            n += 1
            for prop in ("UID", "DTSTAMP", "DTSTART"):
                if prop not in event:
                    errors.append(f"VEVENT {n} without {prop} (RFC 5545 3.6.1)")
            event = None
        elif event is not None:
            event.add(name)
    return errors[:10]


def check_urls(kind: str, urls: list[str]) -> int:
    rc = 0
    for url in urls:
        try:
            body = fetch(url)
            errors = (
                geojson_errors(json.loads(body))
                if kind == "geojson"
                else ical_errors(body.decode())
            )
        except (
            OSError,
            ValueError,
        ) as err:  # a network or parse failure is a result, not a crash
            errors = [f"{type(err).__name__}: {err}"]
        print(f"- {kind} {url}: {'ok' if not errors else f'{len(errors)} errors'}")
        for e in errors:
            print(f"  - {e}")
        rc |= 1 if errors else 0
    return rc


def self_test() -> None:
    assert diff(
        {"a": "FAIL", "b": "SKIP", "c": "PASS"}, {"a": "PASS", "b": "PASS", "c": "FAIL"}
    ) == (["a"], ["b"], ["c"])
    assert diff({}, {"a": "PASS"}) == ([], ["a"], [])
    good = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[17, 48], [18, 48], [18, 49], [17, 48]]],
                },
            }
        ],
    }
    assert geojson_errors(good) == []
    assert geojson_errors({**good, "crs": {}}), "a crs member must be reported"
    swapped = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [48.7, 190]},
            }
        ],
    }
    assert geojson_errors(swapped), "a latitude outside -90..90 must be reported"
    assert geojson_errors({"type": "Feature"}), (
        "a bare Feature is not a FeatureCollection"
    )
    cal = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//y//EN\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTAMP:20261020T000000Z\r\nDTSTART:20261021\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    assert ical_errors(cal) == []
    assert ical_errors(cal.replace("UID:1\r\n", "")) == [
        "VEVENT 1 without UID (RFC 5545 3.6.1)"
    ]
    assert ical_errors(cal.replace("\r\n", "\n")), "LF line ends must be reported"
    print("compare.py self-test ok")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--self-test"]:
        self_test()
        return 0
    if len(argv) >= 2 and argv[0] in ("geojson", "ical"):
        return check_urls(argv[0], argv[1:])
    if len(argv) == 4 and argv[0] == "ogc":
        kind, run, baseline, name = argv
        with open(run, encoding="utf-8") as f:
            prod = broker_parser(kind)(f.read())[0]
        with open(baseline, encoding="utf-8") as f:
            return report(name, prod, json.load(f)["results"])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

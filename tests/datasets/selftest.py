#!/usr/bin/env python3
"""Proof that the dataset validator goes red on each break it is there to catch (T-4087).

A recorded dataset (one CKAN package, one Endpoint serving three OffStreetParking entities in
every representation, the seed in fixtures/seed) is served from memory, no network, and broken
one way at a time. Each case names the only checks that may FAIL:

1. a coherent dataset                -> nothing fails (also with OGC items paged, no numberMatched)
2. an attribute with an empty value  -> ngsi.<Type>.values
3. the same id twice                 -> ngsi.<Type>.ids
4. file.csv two rows short           -> rep.<Type>.csv (a live type has one of slack)
5. file.json answering 403           -> rep.<Type>.json and its CKAN resource line
6. the newest write 3 h old          -> fresh.<Type> (a 5 min schedule)
7. no notification while it changed  -> ws.notify
8. the endpoint extra on another host and an entity missing from its source -> both lines
9. a type of expected/<dataset>.json not served, or older than its maxAge -> expected.types, fresh.<Type>;
   an Attribute it names `numeric` holding text -> ngsi.<Type>.numeric

Every committed expected/<dataset>.json is read too, so a typo in one fails here, not on dev.

    python3 tests/datasets/selftest.py
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.datasets.validate import (  # noqa: E402
    EXPECTED_DIR, Http, Resp, cron_seconds, duration_seconds, load_expected, load_feeds, validate,
)

HERE = Path(__file__).resolve().parent
SEED = HERE / "fixtures" / "seed"
BASE_URL, CKAN_URL = "https://dev.example.test", "https://data.dev.example.test"
BASE = f"{BASE_URL}/api/endpoint/abc/"
TYPE = "OffStreetParking"
CONTEXT = json.dumps({"@context": {"name": "https://schema.org/name"}}).encode()
LINKML = b"id: https://example.test/parking\nname: parking\n"

ENTITIES = [
    {
        "id": f"urn:ngsi-ld:{TYPE}:praha:pr-{n}",
        "type": TYPE,
        "name": {"type": "Property", "value": f"P+R Station {n}"},
        "totalSpotNumber": {"type": "Property", "value": 100},
        "availableSpotNumber": {"type": "Property", "value": 40 + n},
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [14.4 + n / 100, 50.05]}},
    }
    for n in range(3)
]


def xlsx(rows: int) -> bytes:
    sheet = "".join(f'<row r="{i}"><c r="A{i}"/></row>' for i in range(1, rows + 2))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("xl/worksheets/sheet1.xml", f"<worksheet><sheetData>{sheet}</sheetData></worksheet>")
    return buffer.getvalue()


def good() -> dict:
    """The knobs a case turns: everything here is what a coherent publication answers."""
    return {
        "entities": copy.deepcopy(ENTITIES),
        "written_ago": dt.timedelta(minutes=1),
        "csv_rows": 3,
        "expected": {TYPE: {"maxAge": "20m"}},
        "ogc_paged": False,
        "whole_exports": True,
        "json_status": 200,
        "notifications": 1,
        "endpoint_host": BASE_URL,
        "source": " ".join(f'{{"id": "pr-{n}", "name": "P+R Station {n}"}}' for n in range(3)),
    }


class Recorded(Http):
    """The dataset from memory; any URL it does not hold answers 404, and nothing leaves."""

    def __init__(self, state: dict):
        super().__init__()
        self.state = state

    def fetch(self, url, accept="*/*", method="GET", data=None, headers=None, limit=None, timeout=60) -> Resp:
        status, body, hdrs = self._answer(url, method)
        return Resp(status, hdrs, body if isinstance(body, bytes) else json.dumps(body).encode(), 0.05)

    def _entities(self) -> list:
        written = (dt.datetime.now(dt.timezone.utc) - self.state["written_ago"]).isoformat()
        rows = copy.deepcopy(self.state["entities"])
        for e in rows:
            e["name"]["modifiedAt"] = written
        return rows

    def _answer(self, url, method):
        s = self.state
        parts = urlsplit(url)
        path, query = f"{parts.scheme}://{parts.netloc}{parts.path}", parse_qs(parts.query)
        rows = self._entities()
        n = len(rows)
        resources = [
            {"name": "NGSI-LD API", "format": "NGSI-LD", "url": f"{BASE}ngsi-ld/v1/"},
            {"name": "CSV", "format": "CSV", "url": f"{BASE}file.csv"},
            {"name": "JSON", "format": "JSON", "url": f"{BASE}file.json"},
            {"name": "GeoJSON", "format": "GeoJSON", "url": f"{BASE}file.geojson"},
            {"name": "Excel spreadsheet", "format": "XLSX", "url": f"{BASE}file.xlsx"},
            {"name": "Model Context Protocol", "format": "MCP", "url": f"{BASE}mcp"},
            {"name": "Live updates over WebSocket", "format": "WebSocket", "url": f"{BASE}ws"},
            {"name": "OGC API - Features", "format": "OGCFEAT", "url": f"{BASE}ogc/features/"},
            {"name": "JSON Schema artifacts", "format": "JSON", "url": f"{BASE}schema/index.json"},
            {"name": TYPE, "format": "CSV", "datastore_active": True, "id": "ds1",
             "url": f"{CKAN_URL}/datastore/dump/ds1"},
        ]
        if path == f"{CKAN_URL}/api/3/action/package_show":
            extras = [{"key": "endpoint", "value": f"{s['endpoint_host']}/api/endpoint/abc/"}]
            return 200, {"result": {"name": "fixture", "organization": {"name": "praha"},
                                    "extras": extras, "resources": resources}}, {}
        if path == f"{CKAN_URL}/api/3/action/datastore_search":
            return 200, {"result": {"total": n}}, {}
        if path == f"{CKAN_URL}/datastore/dump/ds1":
            return 200, b"_id,entity_id\n1,x\n", {}
        if path == "https://source.example.test/parking.json":
            return 200, s["source"].encode(), {}
        if not path.startswith(BASE):
            return 404, b"", {}
        rest = path[len(BASE):]
        csv = "id,type\n" + "".join(f"{e['id']},{TYPE}\n" for e in rows[: s["csv_rows"]])
        features = [{"type": "Feature", "id": e["id"], "geometry": e["location"]["value"], "properties": {}} for e in rows]
        index = {"models": [{"name": "parking", "version": 1, "types": [TYPE], "artifacts": {
            "context.jsonld": {"sha256": hashlib.sha256(CONTEXT).hexdigest()},
            "model.linkml.yaml": {"sha256": hashlib.sha256(LINKML).hexdigest()},
        }}]}
        answers = {
            "ngsi-ld/v1/": (200, {"type": "EntityTypeList"}),
            "ngsi-ld/v1/types": (200, {"type": "EntityTypeList", "typeList": [TYPE]}),
            "file.csv": (200, csv.encode()),
            "file.json": (s["json_status"], rows if s["json_status"] == 200 else {"title": "AccessDenied"}),
            "file.geojson": (200, {"type": "FeatureCollection", "features": features}),
            "file.xlsx": (200, xlsx(n)),
            "mcp": (200 if method == "POST" else 405, {"jsonrpc": "2.0", "id": 1, "result": {}}),
            "ogc/features/": (200, {"links": []}),
            "ogc/features/conformance": (200, {"conformsTo": []}),
            "ogc/features/collections": (200, {"collections": [{"id": TYPE}]}),
            f"ogc/features/collections/{TYPE}/items": (200, self._ogc(features, query)),
            "schema/index.json": (200, index),
            "schema/v1/context.jsonld": (200, CONTEXT),
            "schema/v1/model.linkml.yaml": (200, LINKML),
        }
        if rest == "ngsi-ld/v1/entities":
            assert query.get("type") == [TYPE], url
            offset = int(query.get("offset", ["0"])[0])
            return 200, rows[offset:], {"ngsild-results-count": str(n)}
        if rest in answers:
            return (*answers[rest], {})
        return 404, b"", {}

    def _ogc(self, features, query):
        if not self.state["ogc_paged"]:
            return {"numberMatched": len(features), "features": features}
        # No numberMatched, two pages over a next link, as dev's Endpoints answer.
        if query.get("page") == ["2"]:
            return {"features": features[1:], "links": []}
        nxt = f"{BASE}ogc/features/collections/{TYPE}/items?page=2"
        return {"features": features[:1], "links": [{"rel": "next", "href": nxt}]}

    def ws(self, url, subscription, seconds):
        assert url == f"{BASE}ws", url
        if subscription.get("q"):
            return 101, 400, 0
        return 101, 201, self.state["notifications"]


CASES = {
    "good": (lambda s: None, set()),
    "ogc counted over its pages": (lambda s: s.update(ogc_paged=True), set()),
    "an expected type missing": (lambda s: s["expected"].update(GtfsStop={"maxAge": None}), {"expected.types"}),
    "older than expected": (lambda s: s["expected"][TYPE].update(maxAge="30s"), {f"fresh.{TYPE}"}),
    # The whole-dataset JSON answers 403, but it is not fetched: only the per-type line fails.
    "403 export, whole exports skipped": (lambda s: s.update(json_status=403, whole_exports=False), {f"rep.{TYPE}.json"}),
    "an empty value the dataset declares": (
        lambda s: (s["entities"][1]["totalSpotNumber"].update(value=""),
                   s["expected"][TYPE].update(emptyAllowed=["totalSpotNumber"])),
        set(),
    ),
    "numbers held": (lambda s: s["expected"][TYPE].update(numeric=["totalSpotNumber"]), set()),
    "a number written as text": (
        lambda s: (s["expected"][TYPE].update(numeric=["totalSpotNumber"]),
                   s["entities"][1]["totalSpotNumber"].update(value="not measured")),
        {f"ngsi.{TYPE}.numeric"},
    ),
    "empty value": (lambda s: s["entities"][1]["totalSpotNumber"].update(value=""), {f"ngsi.{TYPE}.values"}),
    "duplicate id": (lambda s: s["entities"][2].update(id=s["entities"][1]["id"]), {f"ngsi.{TYPE}.ids"}),
    "count mismatch": (lambda s: s.update(csv_rows=1), {f"rep.{TYPE}.csv"}),
    "403 export": (lambda s: s.update(json_status=403), {f"rep.{TYPE}.json", "ckan.resource JSON"}),
    # No expectation: the bound comes from the seed Pipeline's 5 min period.
    "stale type": (lambda s: s.update(written_ago=dt.timedelta(hours=3), expected={}), {f"fresh.{TYPE}"}),
    "ws silence": (lambda s: s.update(notifications=0, written_ago=dt.timedelta(0)), {"ws.notify"}),
    "wrong host, not in source": (
        lambda s: s.update(endpoint_host="https://portal.dev.example.test", source="{}"),
        {"ckan.endpoint_host", f"source.{TYPE}"},
    ),
}


def run(state):
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, "fixture.json").write_text(json.dumps({"types": state["expected"]}), encoding="utf-8")
        return validate(Recorded(state), "fixture", BASE_URL, CKAN_URL, SEED, ws_seconds=0, expected_dir=Path(folder),
                        whole_exports=state["whole_exports"])


def main() -> int:
    # The seed reading the checks rest on: periods, crons, and a type named only in a mapping.
    assert duration_seconds("5m") == 300 and duration_seconds("168h") == 604800 and duration_seconds("") is None
    assert cron_seconds("*/5 * * * *") == 300 and cron_seconds("40 * * * *") == 3600
    assert cron_seconds("40 */6 * * *") == 21600 and cron_seconds("10 3 * * *") == 86400
    assert cron_seconds("20 4 * * 1") == 604800 and cron_seconds("bad") is None
    feeds = load_feeds(SEED, ("praha",))
    assert feeds[TYPE].period == 300 and feeds[TYPE].sources[0]["http"]["url"].endswith("parking.json"), feeds
    assert feeds["GtfsStop"].period == 604800, feeds
    assert load_feeds(SEED, ("nowhere",)) == {}

    for path in sorted(EXPECTED_DIR.glob("*.json")):
        assert load_expected(EXPECTED_DIR, path.stem), f"{path} names no type"

    failures = 0
    for case, (breaks, expected) in CASES.items():
        state = good()
        breaks(state)
        checks = run(state)
        failed = {c.check for c in checks if c.verdict == "FAIL"}
        verdict = "ok" if failed == expected else "WRONG"
        failures += failed != expected
        print(f"{verdict:5} {case:28} failed: {sorted(failed) or '-'}")
        if failed != expected:
            for c in checks:
                print(f"        {c.verdict} {c.check} {c.measured} {c.detail}")
    # T-4109: a source FAIL names the entities it did not find, not the sample's first three.
    state = good()
    state.update(source='{"id": "pr-0", "name": "P+R Station 0"}')
    named = next(c.detail for c in run(state) if c.check == f"source.{TYPE}")
    assert named and ":pr-0" not in named and ":pr-1" in named and ":pr-2" in named, named
    print(f"{len(CASES) - failures}/{len(CASES)} cases held")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

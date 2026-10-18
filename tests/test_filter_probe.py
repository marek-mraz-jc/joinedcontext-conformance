"""T-4467: scripts/filter-probe-dev.py judges each standard's filtered answer against NGSI-LD's.

The answers below are recorded shapes from dev (an AirQualityObserved Endpoint, 2026-10-17):
NGSI-LD keyValues with the count header, OData with `@odata.count`, OGC with `numberMatched`.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("filter_probe", ROOT / "scripts" / "filter-probe-dev.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

ID = "urn:ngsi-ld:AirQualityObserved:banskabystrica.sk:banskabystrica-verejne:eea-SK0263A"
POINT = {"type": "Point", "coordinates": [19.115268, 48.733256]}
ENTITY = {"id": ID, "type": "AirQualityObserved", "location": POINT, "pm10": 8.8,
          "dataProvider": "European Environment Agency", "dateObserved": "2026-10-17T10:00:00Z"}
F = ("pm10", ">=", 8.8)


def ngsi(*entities):
    return fp.rows("ngsi-ld", 200, {"NGSILD-Results-Count": str(len(entities))}, list(entities))


def test_a_number_is_picked_before_a_string_and_the_box_holds_the_point():
    assert fp.pick(ENTITY) == F
    assert fp.pick({"id": "x", "name": "Hlavná 1"}) == ("name", "==", "Hlavná 1")
    assert fp.pick({"id": "x", "flag": True, "note": "a;b"}) is None
    w, s, e, n = fp.box(ENTITY)
    assert w < 19.115268 < e and s < 48.733256 < n
    assert fp.box({"location": {"type": "Polygon"}}) is None


def test_matching_answers_are_ok():
    ref = ngsi(ENTITY)
    odata = fp.rows("odata", 200, {}, {"@odata.count": 1, "value": [{"id": ID, "pm10": 8.8}]})
    ogc = fp.rows("ogc", 200, {}, {"numberMatched": 1, "features": [
        {"id": ID, "geometry": POINT, "properties": {"pm10": 8.8}}]})
    bbox = fp.box(ENTITY)
    # OData says no box (the probe holds it to the unboxed query), so it is judged without one.
    assert [fp.judge(ref, ref, F, bbox), fp.judge(ref, odata, F, None), fp.judge(ref, ogc, F, bbox)] == ["ok"] * 3


def test_a_row_outside_the_filter_a_wrong_total_or_other_ids_are_mismatches():
    ref = ngsi(ENTITY)
    low = fp.rows("odata", 200, {}, {"@odata.count": 1, "value": [{"id": ID, "pm10": 3}]})
    assert fp.judge(ref, low, F, None).startswith("mismatch: 1 of 1")
    far = fp.rows("ogc", 200, {}, {"numberMatched": 1, "features": [
        {"id": ID, "geometry": {"type": "Point", "coordinates": [17.1, 48.1]}, "properties": {"pm10": 9}}]})
    assert fp.judge(ref, far, F, fp.box(ENTITY)).startswith("mismatch")
    more = fp.rows("odata", 200, {}, {"@odata.count": 2, "value": [{"id": ID, "pm10": 9}]})
    assert fp.judge(ref, more, F, None) == "mismatch: total 2 vs NGSI-LD 1"
    other = fp.rows("odata", 200, {}, {"@odata.count": 1, "value": [{"id": "urn:x", "pm10": 9}]})
    assert fp.judge(ref, other, F, None) == "mismatch: other ids than NGSI-LD"


def test_a_failed_answer_is_an_error_never_a_verdict():
    assert fp.judge(ngsi(ENTITY), fp.rows("ogc", 503, {}, {"status": 503}), F, None) == "error: HTTP 503"
    assert fp.judge(ngsi(ENTITY), fp.rows("ogc", 0, {}, None), F, None) == "error: no answer"


def test_the_requests_say_the_same_filter_in_each_standard():
    served = {"ngsi-ld": "https://x/e/ngsi-ld/v1/", "odata": "https://x/e/odata/v4/",
              "ogc": "https://x/e/ogc/features/", "file.geojson": "https://x/e/file.geojson"}
    asks = fp.urls(served, "AirQualityObserved", ("name", "==", "O'Neil"), [1, 2, 3, 4])
    assert "q=name%3D%3D%22O%27Neil%22" in asks["ngsi-ld"] and "georel=intersects" in asks["ngsi-ld"]
    assert "%24filter=name%20eq%20%27O%27%27Neil%27" in asks["odata"]
    assert "bbox=1%2C2%2C3%2C4" in asks["ogc"] and "filter-lang=cql2-text" in asks["ogc"]
    assert asks["file.geojson"].startswith("https://x/e/file.geojson?type=")


def test_the_feed_lists_each_endpoint_with_what_it_serves():
    feed = {"@included": [{"@id": "https://x/api/endpoint/a", "@type": "dcat:Dataset", "dcat:distribution": [
        {"dcat:accessURL": "https://x/api/endpoint/a/ngsi-ld/v1/"},
        {"dcat:accessURL": "https://x/api/endpoint/a/ogc/features/"}]},
        {"@id": "https://x/r", "@type": "dcat:Dataset"}]}
    assert fp.endpoints(feed) == [("https://x/api/endpoint/a", {
        "ngsi-ld": "https://x/api/endpoint/a/ngsi-ld/v1/", "ogc": "https://x/api/endpoint/a/ogc/features/"})]


def recorded(base, inside, other):
    """A dev conversation, answered by what each URL asks."""
    def get(url):
        if url == base + "ngsi-ld/v1/types":
            return 200, {}, {"typeList": ["AirQualityObserved"]}
        if "limit=1&" in url:
            return 200, {}, [inside]
        if "/ngsi-ld/" in url and "%21location" in url:
            return 200, {"NGSILD-Results-Count": "1"}, [{"id": "urn:nowhere", "pm10": 9.0}]
        if "/ogc/" in url:
            return 200, {}, {"numberMatched": 2, "features": [
                {"id": ID, "geometry": POINT, "properties": {"pm10": 8.8}},
                {"id": "urn:nowhere", "geometry": None, "properties": {"pm10": 9.0}}]}
        if "/ngsi-ld/" in url:
            found = [inside] if "georel" in url else [inside, other]
            return 200, {"NGSILD-Results-Count": str(len(found))}, found
        if "/odata/" in url:
            return 200, {}, {"@odata.count": 2, "value": [{"id": e["id"], "pm10": 8.8} for e in (inside, other)]}
        return 404, {}, None
    return type("Recorded", (), {"get": staticmethod(get)})()


def test_odata_is_held_to_the_query_without_the_box_it_cannot_say():
    base = "https://x/e/"
    served = {"ngsi-ld": base + "ngsi-ld/v1/", "odata": base + "odata/v4/", "ogc": base + "ogc/features/"}
    other = {**ENTITY, "id": "urn:other", "location": {"type": "Point", "coordinates": [17.1, 48.1]}}
    cells = fp.probe(recorded(base, ENTITY, other), base, served)
    # OGC's box also keeps the unlocated entity: held to NGSI-LD's boxed plus unlocated answer.
    assert cells["ngsi-ld"] == "ok" and cells["odata"] == "ok" and cells["ogc"] == "ok", cells
    assert cells["file.geojson"] == "not served"
    empty = type("Empty", (), {"get": staticmethod(lambda url: (200, {}, {"typeList": []}))})()
    assert fp.probe(empty, base, served)["odata"] == "skipped: the Endpoint holds no type"


def test_a_feature_without_geometry_is_in_any_ogc_box_and_a_date_is_not_picked():
    ref = ngsi(ENTITY)
    nowhere = fp.rows("ogc", 200, {}, {"numberMatched": 1, "features": [
        {"id": ID, "geometry": None, "properties": {"pm10": 9}}]})
    assert fp.judge(ref, nowhere, F, fp.box(ENTITY)) == "ok"
    assert fp.pick({"id": "x", "endDate": "2026-12-31T19:00:00Z", "name": "Jarmok"}) == ("name", "==", "Jarmok")

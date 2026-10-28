"""The T-4754 request list generator, offline: what it asks of each type and Endpoint, which
datasets it reads, and that its probe stays paced and bounded."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from make_requests import Prober, build, complex_rows, datasets_of, endpoint_rows, numeric_of, point_of, rows_for  # noqa: E402

EP = "https://api.example/api/endpoint/abc/"


def test_a_type_is_asked_on_every_surface_and_near_its_own_point():
    rows = rows_for(EP, "BikeHireDockingStation", (17.1, 48.1))
    kinds = [r[0] for r in rows]
    assert kinds == ["ngsi", "ngsi", "geo", "temporal", "odata", "ogc", "csv", "mcp"]
    geo = rows[2][2]
    assert "georel=near%3BmaxDistance%3D%3D2000" in geo and "coordinates=%5B17.1%2C48.1%5D" in geo
    mcp = json.loads(rows[-1][3])
    assert mcp["method"] == "tools/call" and mcp["params"]["arguments"] == {"type": ["BikeHireDockingStation"], "limit": 5}
    assert all(r[3] == "" for r in rows if r[1] == "GET")
    assert "timerel=after&timeAt=2026-01-01T00%3A00%3A00Z&lastN=5" in rows[3][2], "5.7.4 asks a timerel"


def test_a_type_without_a_point_has_no_geo_question():
    assert "geo" not in [r[0] for r in rows_for(EP, "Article", None)]


def test_only_a_point_with_two_numbers_is_a_point():
    assert point_of({"location": {"type": "Point", "coordinates": [17, 48.5]}}) == (17.0, 48.5)
    for entity in (None, {}, {"location": "x"}, {"location": {"type": "Polygon", "coordinates": [[[0, 0]]]}},
                   {"location": {"type": "Point", "coordinates": [True, 1]}}, {"location": {"type": "Point", "coordinates": [1]}}):
        assert point_of(entity) is None, entity


def test_an_endpoint_is_asked_for_its_artifacts_and_held_over_its_websocket():
    rows = endpoint_rows(EP)
    assert rows[:2] == [
        ("schema", "GET", f"{EP}schema/index.json", ""),
        ("ws", "GET", "wss://api.example/api/endpoint/abc/ws", ""),
    ]
    assert [r[0] for r in rows[2:]] == ["sta.expand", "sta.filter", "ical"]
    assert "%24expand=Datastreams%28%24expand%3DObservations" in rows[2][2]
    assert rows[4][2] == f"{EP}ical/v1/events.ics"


def test_a_dataset_needs_an_endpoint_and_a_datastore_table():
    answer = {"result": {"results": [
        {"name": "ovzdusie", "extras": [{"key": "endpoint", "value": EP}],
         "resources": [{"name": "AirQualityObserved", "datastore_active": True}, {"name": "CSV", "datastore_active": False}]},
        {"name": "no-endpoint", "resources": [{"name": "T", "datastore_active": True}]},
        {"name": "bad-endpoint", "extras": [{"key": "endpoint", "value": "javascript:x"}], "resources": [{"name": "T", "datastore_active": True}]},
        {"name": "no-table", "extras": [{"key": "endpoint", "value": EP}], "resources": []},
    ]}}
    assert datasets_of(answer) == [("ovzdusie", EP, ["AirQualityObserved"])]
    assert datasets_of({}) == []


class Answer:
    def __init__(self, status: int, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class Session:
    """Answers package_search with one dataset of one located type; everything else 200, except
    the temporal question, which this catalogue refuses."""

    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(("GET", url))
        if "package_search?rows=1000" in url:
            return Answer(200, {"result": {"results": [
                {"name": "kola-praha", "extras": [{"key": "endpoint", "value": EP}],
                 "resources": [{"name": "BikeHireDockingStation", "datastore_active": True}]}]}})
        if "limit=1&format=keyValues" in url:
            return Answer(200, [{"id": "urn:x", "location": {"type": "Point", "coordinates": [14.4, 50.1]}, "freeSlots": 4}])
        if "/temporal/" in url:
            return Answer(400)
        return Answer(200)

    def post(self, url, data, headers, timeout):
        self.calls.append(("POST", url))
        return Answer(200)


def test_the_list_carries_each_rows_probed_status_and_the_probe_is_paced():
    session, slept = Session(), []
    lines = list(build("https://ckan.example", Prober(session, 1.2, 500, slept.append)))
    rows = [line.split("\t") for line in lines if not line.startswith("#")]
    assert lines[0].startswith("# T-4754 mix from https://ckan.example: 1 datasets")
    kinds = [r[0] for r in rows]
    assert kinds[:12] == ["schema", "ws", "sta.expand", "sta.filter", "ical", "ngsi", "ngsi", "geo", "temporal", "odata", "ogc", "csv"]
    assert {"ngsi.q-nested", "geo.within", "temporal.aggr", "ogc.cql2", "odata.batch", "mcp.q"} <= set(kinds)
    assert kinds[-1] == "ckan"
    assert {r[0]: r[3] for r in rows if len(r) > 3}["temporal"] == "400", "a refused question is kept as evidence and skipped by the mix"
    assert len(rows[1]) == 3, "a WebSocket is never probed over HTTP"
    assert json.loads(rows[kinds.index("mcp")][4])["method"] == "tools/call"
    assert all(pause == 1.2 for pause in slept) and len(slept) == len(session.calls) - 1


def test_the_probe_stops_at_its_limit_and_says_so():
    session = Session()
    lines = list(build("https://ckan.example", Prober(session, 1.2, 4, lambda _: None)))
    assert len(session.calls) == 4
    assert all(len(line.split("\t")) != 4 or line.split("\t")[3] != "" for line in lines)
    assert not any(line.startswith("ckan") for line in lines)


def test_the_harder_questions_use_the_samples_number_and_point():
    rows = {r[0]: r for r in complex_rows(EP, "Bike", (17.1, 48.1), ("freeSlots", 4.0), "2026-10-21T00:00:00Z", "2026-10-28T00:00:00Z")}
    assert set(rows) == {"ngsi.pick", "ngsi.omit", "ngsi.join", "odata.expand", "geo.within", "geo.intersects", "ogc.bbox",
                         "ngsi.q-nested", "ngsi.count-q", "temporal.aggr", "ogc.cql2", "ogc.sortby", "odata.filter",
                         "odata.apply", "odata.batch", "mcp.q", "csv.q"}
    assert "q=%28freeSlots%3E%3D4%3BfreeSlots%3C%3D8%29%7CfreeSlots%3C4" in rows["ngsi.q-nested"][2], "nested and/or, ; and | encoded"
    assert "georel=within&geometry=Polygon" in rows["geo.within"][2]
    assert "aggrMethods=avg%2Cmin%2Cmax&aggrPeriodDuration=P1D" in rows["temporal.aggr"][2]
    assert "bbox=17.05%2C48.05%2C17.15%2C48.15" in rows["ogc.bbox"][2]
    assert "filter-lang=cql2-text" in rows["ogc.cql2"][2] and "limit=1000" in rows["ogc.sortby"][2]
    batch = json.loads(rows["odata.batch"][3])
    assert rows["odata.batch"][1] == "POST" and len(batch["requests"]) == 2
    assert json.loads(rows["mcp.q"][3])["params"]["arguments"]["q"] == "(freeSlots>=4;freeSlots<=8)|freeSlots<4"
    assert all(r[3] == "" for r in rows.values() if r[1] == "GET")


def test_a_sample_without_numbers_or_a_point_asks_only_the_projections():
    kinds = [r[0] for r in complex_rows(EP, "Article", None, None, "a", "b")]
    assert kinds == ["ngsi.pick", "ngsi.omit", "ngsi.join", "odata.expand"]


def test_only_a_plain_number_with_a_plain_name_is_a_numeric_attribute():
    assert numeric_of({"id": 1, "type": 2, "name": "x", "ok": True, "bad name": 3, "speed": 12}) == ("speed", 12.0)
    assert numeric_of(None) is None and numeric_of({"a": "1"}) is None

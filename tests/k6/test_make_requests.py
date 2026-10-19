"""The T-4754 request list generator, offline: what it asks of each type and Endpoint, which
datasets it reads, and that its probe stays paced and bounded."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from make_requests import Prober, build, datasets_of, endpoint_rows, point_of, rows_for  # noqa: E402

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
    assert endpoint_rows(EP) == [
        ("schema", "GET", f"{EP}schema/index.json", ""),
        ("ws", "GET", "wss://api.example/api/endpoint/abc/ws", ""),
    ]


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
        if "attrs=location" in url:
            return Answer(200, [{"location": {"type": "Point", "coordinates": [14.4, 50.1]}}])
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
    assert [r[0] for r in rows] == ["schema", "ws", "ngsi", "ngsi", "geo", "temporal", "odata", "ogc", "csv", "mcp", "ckan"]
    assert {r[0]: r[3] for r in rows if len(r) > 3}["temporal"] == "400", "a refused question is kept as evidence and skipped by the mix"
    assert len(rows[1]) == 3, "a WebSocket is never probed over HTTP"
    assert json.loads(rows[9][4])["method"] == "tools/call"
    assert all(pause == 1.2 for pause in slept) and len(slept) == len(session.calls) - 1


def test_the_probe_stops_at_its_limit_and_says_so():
    session = Session()
    lines = list(build("https://ckan.example", Prober(session, 1.2, 4, lambda _: None)))
    assert len(session.calls) == 4
    assert all(len(line.split("\t")) != 4 or line.split("\t")[3] != "" for line in lines)
    assert not any(line.startswith("ckan") for line in lines)

"""The verdicts of `standards_sweep.py` (T-4279) on canned answers, so the fast lane proves each one
can go red without a catalogue or a gateway."""

from __future__ import annotations

import json

import requests
from standards_sweep import probe_http, rows, sweep, table

ROOT = "https://dev.joinedcontext.com/api/endpoint/abc/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Answer:
    def __init__(self, status, kind, body, headers=None):
        self.status_code, self.content = status, body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"content-type": kind, **(headers or {})}

    def json(self):
        return json.loads(self.content)


class Http:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def get(self, url, timeout):
        self.asked.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


def resource(path, media, name="NGSI-LD API (Supplier)"):
    return {"name": name, "url": ROOT + path, "mimetype": media, "resource_type": "api"}


def test_each_media_type_is_counted_by_its_own_shape():
    assert rows(ROOT + "file.csv", "text/csv", "﻿a,b\n1,2\n3,4\n".encode(), {}) == 2
    assert rows(ROOT + "file.geojson", "application/geo+json", b'{"features": [{}, {}, {}]}', {}) == 3
    assert rows(ROOT + "odata/v4/T", "application/json", b'{"value": [{}]}', {}) == 1
    assert rows(ROOT + "file.json", "application/json", b"[{}, {}]", {}) == 2
    assert rows(ROOT + "ngsi-ld/v1/entities?type=T", "application/ld+json", b"[{}]", {"NGSILD-Results-Count": "57"}) == 57
    assert rows(ROOT + "file.xlsx", XLSX, b"PK\x03\x04rest", {}) == 8
    assert rows(ROOT + "file.xlsx", XLSX, b"<html>", {}) is None
    assert rows(ROOT + "file.json", "application/json", b"<html>", {}) is None


def test_the_ngsi_ld_count_is_asked_for_and_an_empty_answer_is_not_ok():
    r = resource("ngsi-ld/v1/entities?type=Supplier", "application/ld+json")
    http = Http({r["url"] + "&count=true": Answer(200, "application/ld+json", [], {"NGSILD-Results-Count": "0"})})
    assert probe_http(http, r) == ("EMPTY", "200 0 rows")
    assert http.asked == [r["url"] + "&count=true"]


def test_a_refusal_a_wrong_media_type_and_a_dead_host_fail_with_what_answered():
    r = resource("file.csv", "text/csv", "CSV (Supplier)")
    problem = {"type": "https://uri.etsi.org/ngsi-ld/errors/LdContextNotAvailable", "title": "LdContextNotAvailable"}
    assert probe_http(Http({r["url"]: Answer(503, "application/problem+json", problem)}), r) == ("FAIL", "503 LdContextNotAvailable")
    assert probe_http(Http({r["url"]: Answer(200, "text/html", b"<html>")}), r) == ("FAIL", "200 text/html, listed as text/csv")
    assert probe_http(Http({r["url"]: requests.ConnectionError()}), r) == ("FAIL", "ConnectionError")
    assert probe_http(Http({r["url"]: Answer(200, "text/csv; charset=utf-8", b"a\n1\n")}), r) == ("OK", "200 1 rows")


def test_the_sweep_routes_ws_and_mcp_and_lists_a_dataset_without_apis():
    packages = [
        {"name": "bbsk-b", "resources": [{"url": "https://opendata.bbsk.sk/x.csv", "format": "CSV"}]},
        {"name": "bbsk-a", "resources": [
            resource("file.csv", "text/csv", "CSV (Supplier)"),
            {"name": "WebSocket (Supplier)", "url": "wss://dev.joinedcontext.com/api/endpoint/abc/ws", "resource_type": "api"},
            {"name": "Model Context Protocol (Supplier)", "url": ROOT + "mcp", "resource_type": "api"}]},
    ]
    seen = []
    results = sweep(packages, lambda r: ("OK", "200 3 rows"),
                    lambda url, kind: seen.append(kind) or "x-subscribe answered 403", lambda url: None)
    assert results == [
        ("bbsk-a", "CSV", "OK", "200 3 rows"),
        ("bbsk-a", "WebSocket", "FAIL", "x-subscribe answered 403"),
        ("bbsk-a", "Model Context Protocol", "OK", "initialize 200"),
        ("bbsk-b", "-", "none", "no api distribution"),
    ]
    assert seen == ["Supplier"]
    report = table(results)
    assert "| bbsk-a | WebSocket | FAIL | x-subscribe answered 403 |" in report
    assert "tally: FAIL 1, OK 2, none 1" in report

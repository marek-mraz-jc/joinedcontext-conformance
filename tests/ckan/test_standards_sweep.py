"""The verdicts of `standards_sweep.py` (T-4279) on canned answers, so the fast lane proves each one
can go red without a catalogue or a gateway."""

from __future__ import annotations

import json

import requests
from standards_sweep import probe_http, rows, sweep, table

ROOT = "https://dev.joinedcontext.com/api/endpoint/abc/"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PARQUET = "application/vnd.apache.parquet"


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
    assert rows(ROOT + "file.parquet", PARQUET, b"PAR1rowsPAR1", {}) == 12
    assert rows(ROOT + "file.parquet", PARQUET, b"PAR1", {}) is None
    assert rows(ROOT + "file.parquet", PARQUET, b"PAR1 cut off", {}) is None
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


# T-4386: the hourly sweep of all six publishers. A 404 fails; a 401 is a distribution closed to
# the anonymous sweep and passes; a 429 from the edge's rate limit is asked again after a wait and
# counts only when it outlasts every wait.
class Throttled(Http):
    def get(self, url, timeout):
        self.asked.append(url)
        return self.answers[url].pop(0)


def test_a_missing_distribution_fails_and_a_closed_one_passes():
    r = resource("file.csv", "text/csv", "CSV (Supplier)")
    assert probe_http(Http({r["url"]: Answer(404, "application/json", {"title": "ResourceNotFound"})}), r) \
        == ("FAIL", "404 ResourceNotFound")
    assert probe_http(Http({r["url"]: Answer(401, "application/json", {})}), r) == ("RESTRICTED", "401")


def test_a_rate_limited_read_is_asked_again_after_the_wait():
    r = resource("file.csv", "text/csv", "CSV (Supplier)")
    waited = []
    http = Throttled({r["url"]: [Answer(429, "text/plain", b"", {"Retry-After": "7"}),
                                 Answer(429, "text/plain", b""),
                                 Answer(200, "text/csv", b"a\n1\n")]})
    assert probe_http(http, r, waits=(2.0, 10.0, 30.0), sleep=waited.append) == ("OK", "200 1 rows")
    assert waited == [7.0, 10.0] and len(http.asked) == 3
    capped = Throttled({r["url"]: [Answer(429, "text/plain", b"", {"Retry-After": "3600"}), Answer(200, "text/csv", b"a\n")]})
    waited.clear()
    probe_http(capped, r, waits=(2.0,), sleep=waited.append)
    assert waited == [60.0], "a Retry-After past the cap waits the cap"


def test_a_rate_limit_that_outlasts_every_wait_fails():
    r = resource("file.csv", "text/csv", "CSV (Supplier)")
    http = Throttled({r["url"]: [Answer(429, "text/plain", b"")] * 3})
    assert probe_http(http, r, waits=(1.0, 1.0), sleep=lambda s: None) == ("FAIL", "429 after 3 tries")


def test_the_exit_code_passes_ok_and_restricted_only(monkeypatch, tmp_path):
    import standards_sweep
    import surfaces

    def run(verdicts):
        class Reader:
            http = None

            def __init__(self, url):
                pass

            def action(self, action, **params):
                rs = [resource(f"file{i}.csv", "text/csv", f"CSV{i} (Supplier)") for i in range(len(verdicts))]
                return {"results": [{"name": params["fq"].split(":")[1] + "-a", "resources": rs}]}

        answers = iter(verdicts)
        monkeypatch.setattr(standards_sweep, "Reader", Reader)
        monkeypatch.setattr(standards_sweep, "probe_http", lambda http, r: (next(answers), "x"))
        monkeypatch.setattr(surfaces, "answers_ws", lambda *a: None)
        monkeypatch.setattr(surfaces, "answers_mcp", lambda *a: None)
        out, xml = tmp_path / "t.md", tmp_path / "t.xml"
        code = standards_sweep.main(["--ckan", "https://c", "--pace", "0", "--out", str(out), "--junit", str(xml), "bbsk"])
        return code, out.read_text(), xml.read_text()

    code, report, xml = run(["OK", "RESTRICTED"])
    assert code == 0 and "| bbsk-a | CSV1 | RESTRICTED | x |" in report and "<failure" not in xml
    for red in ("FAIL", "EMPTY"):
        code, _, xml = run(["OK", red])
        assert code == 1 and xml.count("<failure") == 1 and 'name="bbsk-a CSV1"' in xml


# T-4429: an Endpoint's own publisher (jcctl ckan.rs) sets no resource_type, so the sweep listed
# every such dataset as `none` and called nothing. It keys on the Endpoint URL instead.
def test_an_endpoint_published_dataset_is_swept_by_its_urls():
    published = [
        {"name": "NGSI-LD API", "url": ROOT + "ngsi-ld/v1/", "mimetype": "application/ld+json", "resource_type": None},
        {"name": "CSV", "url": ROOT + "file.csv", "mimetype": "text/csv", "resource_type": None},
        {"name": "Live updates over WebSocket", "url": ROOT + "ws", "mimetype": "application/json"},
        {"name": "Model Context Protocol", "url": ROOT + "mcp", "mimetype": "application/json"},
        {"name": "Data model: SHACL", "url": ROOT + "schema/v1/model.shacl.ttl", "mimetype": "text/turtle"},
        {"name": "Event", "url": "https://data.dev.joinedcontext.com/datastore/dump/x", "url_type": "datastore"},
    ]
    called, seen = [], []
    results = sweep([{"name": "bb-podujatia", "resources": published}],
                    lambda r: called.append(r["url"]) or ("OK", "200"),
                    lambda url, kind: seen.append((url, kind)) or None, lambda url: None)
    assert [(d, v) for _, d, v, _ in results] == [
        ("NGSI-LD API", "OK"), ("CSV", "OK"), ("Live updates over WebSocket", "OK"), ("Model Context Protocol", "OK")]
    assert called == [ROOT + "ngsi-ld/v1/", ROOT + "file.csv"]
    assert seen == [(ROOT + "ws", "")]


def test_a_base_url_passes_on_a_json_landing_page_and_fails_on_anything_else():
    r = {"name": "NGSI-LD API", "url": ROOT + "ngsi-ld/v1/", "mimetype": "application/ld+json"}
    assert probe_http(Http({r["url"]: Answer(200, "application/json", {"entities": ROOT})}), r) \
        == ("OK", "200 application/json landing page")
    assert probe_http(Http({r["url"]: Answer(200, "text/html", b"<html>")}), r) \
        == ("FAIL", "200 text/html, not a JSON landing page")
    assert probe_http(Http({r["url"]: Answer(404, "application/json", {"title": "NotFound"})}), r) \
        == ("FAIL", "404 NotFound")

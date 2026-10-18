"""The scoring of the agent benchmark (T-4496) on recorded `claude -p` transcripts: tool calls
and tokens counted from the stream, numbers read the way people write them, and an answer
matched only within the question's tolerance."""

from __future__ import annotations

import importlib.util
import sys

import pytest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "agent-questions" / "agent_bench.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent-bench"

specification = importlib.util.spec_from_file_location("agent_bench", SCRIPT)
assert specification and specification.loader
bench = importlib.util.module_from_spec(specification)
sys.modules["agent_bench"] = bench
specification.loader.exec_module(bench)


def lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text(encoding="utf-8").splitlines()


def test_calls_tokens_and_answer_come_from_the_stream():
    record = bench.transcript(lines("two-calls.jsonl"))
    assert record["calls"] == ["mcp__opendata__list_endpoints", "mcp__opendata__query_entities"]
    assert record["tokens"] == 8 + 1000 + 2000 + 120
    assert record["error"] is None
    assert "13,9" in record["answer"]


def test_a_stream_without_a_result_is_an_error_not_a_score():
    record = bench.transcript(lines("no-result.jsonl"))
    assert record["tokens"] is None and record["error"]


def test_numbers_are_read_with_either_separator():
    found = bench.numbers_in("13,9 µg/m³, 1 605 stations, 4,862 spaces, 220.023.032 €, 1,234.5")
    assert {13.9, 1605.0, 4862.0, 220023032.0, 1234.5} <= found


def test_an_answer_matches_only_within_its_tolerance():
    answer = bench.transcript(lines("two-calls.jsonl"))["answer"]
    assert bench.matches(answer, [13.915], {"decimals": 1})
    assert not bench.matches(answer, [14.2], {"decimals": 1})
    assert bench.matches(answer, [1600.0], {"relative": 0.05})
    assert not bench.matches(answer, [1700.0], {"relative": 0.05})
    assert not bench.matches("no number here", [5.0], {})
    # Several stations at the newest time: any one of them is the answer.
    assert bench.matches(answer, [35.26, 13.915], {"decimals": 1})


def test_pick_takes_the_newest_the_largest_and_the_sum():
    entities = [
        {"pm10": 10.0, "dateObserved": "2026-10-17T18:00:00Z"},
        {"pm10": 13.9, "dateObserved": "2026-10-17T19:00:00Z"},
        {"pm10": 35.3, "dateObserved": "2026-10-17T19:00:00Z"},
        {"pm10": "n/a", "dateObserved": "2026-10-17T20:00:00Z"},
    ]
    assert bench.pick(entities, {"type": "T", "pick": "latest", "attr": "pm10", "by": "dateObserved"}) == [13.9, 35.3]
    assert bench.pick(entities, {"type": "T", "pick": "max", "attr": "pm10"}) == [35.3]
    assert bench.pick(entities, {"type": "T", "pick": "sum", "attr": "pm10"}) == pytest.approx([59.2])


def test_every_question_names_a_read_and_a_rule():
    spec = bench.load()
    ids = [q["id"] for q in spec["questions"]]
    assert len(ids) == 26 and len(set(ids)) == 26
    for question in spec["questions"]:
        assert question["expect"]["pick"] in {"count", "sum", "max", "latest", "text", "aggregate"}
        assert question["expect"]["pick"] == "count" or question["expect"].get("attr")
    # T-4533: the questions about a parameter say which, and a text answer is matched as text.
    uses = {q["id"]: q.get("uses") for q in spec["questions"] if q.get("uses")}
    assert sorted(name for names in uses.values() for name in names[:1]) == [
        "aggrMethods", "datasetId", "georel", "join", "lang"]
    for question in spec["questions"]:
        assert (question["expect"]["pick"] == "text") == bool(question["match"].get("text"))
    # T-4570: the events question names the Event Endpoint and a date filter.
    events = next(q for q in spec["questions"] if q["id"] == "bb-events-weekend")
    assert events["expect"]["type"] == "Event" and events["expect"]["slug"] in events["reads"]["endpoint"]


def test_a_parameter_counts_as_used_only_when_a_call_passed_it():
    used = bench.transcript(lines("join-used.jsonl"))
    assert used["inputs"][0]["joinLevel"] == 2
    assert bench.used(used["inputs"], ["join", "joinLevel"])
    assert not bench.used(used["inputs"], ["georel"])
    unused = bench.transcript(lines("two-calls.jsonl"))
    assert not bench.used(unused["inputs"], ["join", "joinLevel"])
    assert not bench.used([], ["lang"])
    # nested, as a subscription's or a fragment's argument would carry it
    assert bench.used([{"subscription": {"lang": "sk"}}], ["lang"])
    assert not bench.used([{"q": "lang==sk"}], ["lang"]), "a value is not a parameter"


def test_the_table_says_whether_the_parameter_was_used(tmp_path):
    for name in ("join-used.jsonl", "two-calls.jsonl"):
        (tmp_path / name).write_text((FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "expected.json").write_text('{"join-used": [41.0], "two-calls": [13.9]}', encoding="utf-8")
    questions = [
        {"id": "join-used", "match": {}, "uses": ["join", "joinLevel"]},
        {"id": "two-calls", "match": {"decimals": 1}, "uses": ["aggrMethods"]},
    ]
    rows = {row["id"]: row for row in bench.score(tmp_path, questions)}
    assert rows["join-used"]["correct"] and rows["join-used"]["used"] is True
    assert rows["two-calls"]["correct"] and rows["two-calls"]["used"] is False
    table = bench.table(list(rows.values()))
    assert "| join-used | yes | 1 | 2465 | yes | [41.0] |" in table
    assert "| two-calls | yes | 2 | 3128 | no | [13.9] |" in table
    rows = bench.score(tmp_path, [{"id": "two-calls", "match": {"decimals": 1}}])
    assert rows[0]["used"] is None and "| - |" in bench.table(rows)


def test_a_text_answer_matches_whatever_its_case_and_spacing():
    rule = {"text": True}
    assert bench.matches("It is called **station  SK0263A, Urban background**.", ["Station SK0263A, urban background"], rule)
    assert not bench.matches("It is called Stanica SK0263A, mestské pozadie.", ["Station SK0263A, urban background"], rule)
    assert not bench.matches("", ["x"], rule)


def test_text_join_and_aggregate_picks():
    stations = [
        {"name": {"languageMap": {"sk": "Stanica A", "en": "Station A"}}},
        {"name": {"languageMap": {"sk": "Stanica B"}}},
        {"name": "Plain C"},
    ]
    assert bench.pick(stations, {"type": "T", "pick": "text", "attr": "name", "lang": "en"}) == ["Plain C", "Station A"]
    with pytest.raises(ValueError):
        bench.pick(stations[1:2], {"type": "T", "pick": "text", "attr": "name", "lang": "en"})
    orgs = [{"refMunicipality": "urn:m1"}, {"refMunicipality": "urn:m2"}, {"refMunicipality": "urn:m1"}, {}]
    assert bench.joined(orgs, [{"id": "urn:m1"}, {"id": "urn:m9"}], "refMunicipality") == 2
    assert bench.joined(orgs, [], "refMunicipality") == 0
    expect = {"attr": "pm10", "method": "avg", "entity": "urn:x"}
    body = {"pm10": {"type": "Property", "avg": [[9.9589, "2026-10-17T00:00:00Z", "2026-10-18T00:00:00Z"]]}}
    assert bench.aggregated(body, expect) == [9.9589]
    with pytest.raises(ValueError):
        bench.aggregated({"pm10": {"type": "Property", "avg": []}}, expect)


def test_a_day_is_the_whole_utc_day():
    now = bench.datetime(2026, 1, 1, 0, 30, tzinfo=bench.timezone.utc)
    assert bench.day(-1, now) == ("2025-12-31T00:00:00Z", "2026-01-01T00:00:00Z")
    assert bench.day(0, now) == ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")


def test_a_count_is_read_from_the_header_whatever_its_case(monkeypatch):
    import email.message
    import io

    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(b"[]")
            self.headers = email.message.Message()
            self.headers["ngsild-results-count"] = "61"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    asked = []
    monkeypatch.setattr(bench.urllib.request, "urlopen", lambda url, timeout: asked.append(url) or Response())
    expect = {"slug": "s", "type": "Organization", "pick": "count",
              "params": {"georel": "near;maxDistance==1000", "geometry": "Point", "coordinates": "[19.1,48.7]"}}
    assert bench.fetch("https://dev.example", expect) == [61.0]
    assert "georel=near%3BmaxDistance%3D%3D1000" in asked[0] and "count=true" in asked[0]


def test_this_weekend_is_today_on_a_weekend_and_the_coming_one_otherwise():
    at = lambda d: bench.datetime(2026, 10, d, 12, tzinfo=bench.timezone.utc)
    assert bench.weekend(at(14)) == ("2026-10-17", "2026-10-19"), "a Wednesday"
    assert bench.weekend(at(17)) == ("2026-10-17", "2026-10-19"), "the Saturday"
    assert bench.weekend(at(18)) == ("2026-10-17", "2026-10-19"), "the Sunday"
    assert bench.weekend(at(19)) == ("2026-10-24", "2026-10-26"), "the Monday after"


def test_a_weekend_question_reads_with_its_dates(monkeypatch):
    import io

    asked = []

    class Response(io.BytesIO):
        headers = None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def urlopen(url, timeout):
        asked.append(url)
        body = b'[{"id": "urn:ngsi-ld:Event:1", "name": {"languageMap": {"sk": "Sila rastl\xc3\xadn"}}}]' if not asked[1:] else b"[]"
        return Response(body)

    monkeypatch.setattr(bench.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bench, "weekend", lambda: ("2026-10-24", "2026-10-26"))
    expect = {"slug": "s", "type": "Event", "pick": "text", "attr": "name", "lang": "sk", "weekend": "startDate"}
    assert bench.fetch("https://dev.example", expect) == ["Sila rastlín"]
    query = bench.urllib.parse.parse_qs(bench.urllib.parse.urlsplit(asked[0]).query)
    assert query["q"] == ['startDate>="2026-10-24";startDate<"2026-10-26"']


def test_a_read_counts_only_on_the_named_endpoint_with_a_matching_filter():
    reads = {"endpoint": ["l3nomv", "Events — Banská Bystrica"], "q": "startDate|endDate"}
    assert bench.read_as([{"endpoint": "l3nomv", "type": ["Event"], "q": 'startDate>="2026-10-24"'}], reads)
    assert bench.read_as([{}, {"endpoint": "Events — Banská Bystrica", "q": 'endDate<"2026-10-26"'}], reads)
    assert not bench.read_as([{"endpoint": "l3nomv", "type": ["Event"]}], reads), "no date filter"
    assert not bench.read_as([{"endpoint": "news", "q": 'startDate>="2026-10-24"'}], reads), "another Endpoint"
    assert not bench.read_as([], reads)

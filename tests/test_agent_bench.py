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
    assert len(ids) == 20 and len(set(ids)) == 20
    for question in spec["questions"]:
        assert question["expect"]["pick"] in {"count", "sum", "max", "latest"}
        assert question["expect"]["pick"] == "count" or question["expect"].get("attr")

"""The multi-step scenario runner of claude_code_run.py (T-6235): step split, per-step check, verdict table.

fixtures/T-6235-manual-model.jsonl is the recorded live session of scenarios-wave/T-6235.yaml against dev's
full-access connector of t6025-full (scrubbed); the synthetic lines below cover the check's edge cases.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import claude_code
import claude_code_run as ccr

HERE = Path(__file__).parent
SCENARIO = HERE / "scenarios-wave" / "T-6235.yaml"
RECORDED = HERE / "fixtures" / "T-6235-manual-model.jsonl"
# Recorded live sessions, (scenario id, transcript): T-6240 is scenario 7, pipeline create.
SESSIONS = [
    ("T-6235", RECORDED),
    ("T-6240", HERE / "fixtures" / "T-6240-pipeline-create.jsonl"),
]


def _session(*steps: tuple[list[tuple[str, bool]], str]) -> list[str]:
    """Stream-json lines: per step, tool calls (name, refused) then a result with the answer."""
    lines = []
    for n, (calls, answer) in enumerate(steps):
        for i, (tool, refused) in enumerate(calls):
            uid = f"u{n}-{i}"
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-10-31T22:00:00.000Z",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": uid,
                                    "name": f"mcp__jc__{tool}",
                                    "input": {},
                                }
                            ]
                        },
                    }
                )
            )
            lines.append(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-10-31T22:00:00.250Z",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": uid,
                                    "is_error": refused,
                                    "content": [{"type": "text", "text": "x" * 10}],
                                }
                            ]
                        },
                    }
                )
            )
        lines.append(
            json.dumps(
                {
                    "type": "result",
                    "result": answer,
                    "num_turns": len(calls) + 1,
                    "duration_ms": 1000 + n,
                }
            )
        )
    return lines


def test_a_session_splits_into_one_step_per_result():
    found = ccr.steps(
        _session(
            ([("jc_schema_get", False)], "a"),
            ([], "b"),
            ([("jc_change_plan", True), ("jc_change_plan", False)], "c"),
        )
    )
    assert [s.number for s in found] == [1, 2, 3]
    assert [s.tools() for s in found] == [
        ["jc_schema_get"],
        [],
        ["jc_change_plan", "jc_change_plan"],
    ]
    assert [s.run.answer for s in found] == ["a", "b", "c"]
    assert found[2].ms == 1002
    assert found[2].run.calls[1].ms == 250


def test_the_check_names_what_a_step_misses():
    (step,) = ccr.steps(
        _session(([("jc_change_plan", True), ("jc_change_apply", False)], "it failed"))
    )
    assert ccr.check(
        step,
        {
            "tools": ["jc_change_plan"],
            "called": ["jc_change_get"],
            "deny": ["jc_change_apply"],
            "answer": "valid",
        },
    ) == [
        "jc_change_plan not answered",
        "jc_change_get not called",
        "jc_change_apply called",
        "answer does not match",
    ]
    # A refusal is enough for `called`: "not there yet" can be the right answer.
    assert ccr.check(step, {"called": ["jc_change_plan"], "answer": "FAILED"}) == []


def test_the_messages_carry_every_step_with_the_project_filled_in():
    scenario = ccr.load_scenario(SCENARIO)
    lines = ccr.messages(scenario).splitlines()
    assert len(lines) == len(scenario["steps"]) >= 3
    first = json.loads(lines[0])
    assert first["type"] == "user" and "t6025-full" in first["message"]["content"]
    assert "{project}" not in "".join(lines)


def test_a_scenario_without_steps_is_refused(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("id: X\nproject: p\nsteps: []\n")
    with pytest.raises(SystemExit, match="steps"):
        ccr.load_scenario(bad)


@pytest.mark.parametrize(("sid", "recorded"), SESSIONS)
def test_the_recorded_session_gives_the_same_verdicts_again(tmp_path, sid, recorded):
    scenario = ccr.load_scenario(HERE / "scenarios-wave" / f"{sid}.yaml")
    shutil.copy(recorded, tmp_path / f"{sid}.jsonl")
    shutil.copy(HERE / "fixtures" / f"{sid}-verdicts.yaml", tmp_path / "verdicts.yaml")
    first = ccr.verdict(scenario, tmp_path)
    assert first == ccr.verdict(scenario, tmp_path)
    assert first == (HERE / "fixtures" / f"{sid}-verdict.md").read_text(
        encoding="utf-8"
    )
    assert first.count("\n") == 2 + len(scenario["steps"])


def test_a_transcript_short_of_steps_is_refused(tmp_path):
    scenario = ccr.load_scenario(SCENARIO)
    (tmp_path / "T-6235.jsonl").write_text("\n".join(_session(([], "only one"))))
    with pytest.raises(SystemExit, match="1 steps answered"):
        ccr.verdict(scenario, tmp_path)


@pytest.mark.parametrize(("sid", "recorded"), SESSIONS)
def test_the_recorded_session_holds_no_token(sid, recorded):
    text = recorded.read_text(encoding="utf-8")
    assert claude_code.scrub(text) == text

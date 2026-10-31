"""The per-step check of claude_code.py `wave` / `verdict` (T-6235) and its recorded session.

fixtures/T-6235/, T-6240/, T-6245/, T-6250/ and T-6253/ step-NN.jsonl are the recorded live sessions of scenarios-wave/<id>.yaml against dev's
full-access connector of t6025-full (scrubbed), one file per step; the synthetic lines below cover the
check's edge cases.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import claude_code

HERE = Path(__file__).parent
SCENARIO = HERE / "scenarios-wave" / "T-6235.yaml"
RECORDED = HERE / "fixtures" / "T-6235"
# Each recorded session (fixtures/<id>/step-NN.jsonl of scenarios-wave/<id>.yaml): the marks with its
# verdicts.yaml, then unjudged.
SESSIONS = {
    "T-6235": (
        ["ok", "ok", "defect", "defect", "defect", "ok"],
        ["ok", "ok", "check", "check", "check", "ok"],
    ),
    "T-6240": (
        ["ok", "ok", "defect", "defect", "defect", "ok", "ok"],
        ["ok", "ok", "ok", "ok", "check", "ok", "check"],
    ),
    "T-6245": (
        ["defect", "defect", "defect", "defect", "defect", "defect", "defect", "ok"],
        ["ok", "check", "ok", "ok", "check", "check", "ok", "ok"],
    ),
    "T-6250": (
        ["ok", "ok", "defect", "defect", "ok", "ok", "defect"],
        ["ok", "ok", "ok", "ok", "check", "check", "ok"],
    ),
    "T-6253": (
        ["ok", "ok", "defect", "ok", "ok", "ok"],
        ["ok", "ok", "ok", "ok", "ok", "ok"],
    ),
}


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


def _step(calls: list[tuple[str, bool]], answer: str) -> claude_code.Run:
    return claude_code.parse(_session((calls, answer)), "step-01")


def test_the_check_names_what_a_step_misses():
    run = _step([("jc_change_plan", True), ("jc_change_apply", False)], "it failed")
    assert claude_code.check(
        run,
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
    assert (
        claude_code.check(run, {"called": ["jc_change_plan"], "answer": "FAILED"}) == []
    )


def test_an_answered_call_the_step_refuses_is_named():
    # T-6253: a read of another project must come back refused; one the server answered is a leak.
    leak = claude_code.Call(
        "mcp__jc__jc_resource_list", 0, args='{"project":"helsinki"}'
    )
    door = claude_code.Call(
        "mcp__jc__jc_data_query", 0, refused=True, args='{"project":"helsinki"}'
    )
    own = claude_code.Call(
        "mcp__jc__jc_change_list", 0, args='{"project":"t6025-full"}'
    )
    run = claude_code.Run("step-04", calls=[leak, door, own], answer="out of scope")
    spec = {"refuse": ['"project":\\s*"(?!t6025-full")'], "answer": "scope"}
    assert claude_code.check(run, spec) == [
        'jc_resource_list {"project":"helsinki"} answered'
    ]
    assert (
        claude_code.check(claude_code.Run("s", calls=[door, own], answer="scope"), spec)
        == []
    )


def test_a_step_the_check_fails_reads_check_until_judged(tmp_path):
    (tmp_path / "step-01.jsonl").write_text(
        "\n".join(_session(([("jc_schema_get", False)], "it is LinkML"))),
        encoding="utf-8",
    )
    (tmp_path / "step-02.jsonl").write_text(
        "\n".join(_session(([("jc_change_apply", False)], "done"))), encoding="utf-8"
    )
    scenario = tmp_path / "w.yaml"
    scenario.write_text(
        "project: p\nsteps:\n"
        "  - {says: one, tools: [jc_schema_get], answer: linkml}\n"
        "  - {says: two, deny: [jc_change_apply]}\n",
        encoding="utf-8",
    )
    rows = claude_code.verdict(tmp_path, scenario).splitlines()
    assert rows[2].endswith("| ok | own check holds |")
    assert rows[3].endswith("| check | jc_change_apply called |")
    (tmp_path / "verdicts.yaml").write_text(
        "step-02: {verdict: model-error, proof: applied unasked}\n", encoding="utf-8"
    )
    assert (
        claude_code.verdict(tmp_path, scenario)
        .splitlines()[3]
        .endswith("| model-error | applied unasked |")
    )


def test_the_wave_fills_in_the_project_of_the_scenario_file():
    said = claude_code.steps(SCENARIO)
    assert "t6025-full" in said[0]
    assert not any("{project}" in s for s in said)


def test_a_project_placeholder_without_a_project_is_refused(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("steps:\n  - says: in {project}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has no `project`"):
        claude_code.steps(bad)


def test_an_answer_that_is_no_regular_expression_is_refused(tmp_path):
    bad = tmp_path / "x.yaml"
    bad.write_text("steps:\n  - {says: one, answer: '(open'}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no regular expression"):
        claude_code.steps(bad)


@pytest.mark.parametrize("task", SESSIONS)
def test_the_recorded_session_gives_the_same_verdicts_again(tmp_path, task):
    recorded, scenario = (
        HERE / "fixtures" / task,
        HERE / "scenarios-wave" / f"{task}.yaml",
    )
    for f in recorded.glob("step-*.jsonl"):
        shutil.copy(f, tmp_path / f.name)
    shutil.copy(recorded / "verdicts.yaml", tmp_path / "verdicts.yaml")
    first = claude_code.verdict(tmp_path, scenario)
    assert first == claude_code.verdict(tmp_path, scenario)
    assert first == (recorded / "verdict.md").read_text(encoding="utf-8")
    marks = [row.split(" | ")[4] for row in first.splitlines()[2:]]
    assert marks == SESSIONS[task][0]


@pytest.mark.parametrize("task", SESSIONS)
def test_the_recorded_session_unjudged_marks_the_refused_steps_check(tmp_path, task):
    for f in (HERE / "fixtures" / task).glob("step-*.jsonl"):
        shutil.copy(f, tmp_path / f.name)
    scenario = HERE / "scenarios-wave" / f"{task}.yaml"
    marks = [
        row.split(" | ")[4]
        for row in claude_code.verdict(tmp_path, scenario).splitlines()[2:]
    ]
    assert marks == SESSIONS[task][1]


def test_step_files_short_of_the_scenario_are_refused(tmp_path):
    shutil.copy(RECORDED / "step-01.jsonl", tmp_path / "step-01.jsonl")
    with pytest.raises(SystemExit, match="1 step files"):
        claude_code.verdict(tmp_path, SCENARIO)


@pytest.mark.parametrize("task", SESSIONS)
def test_the_recorded_session_holds_no_token(task):
    for f in (HERE / "fixtures" / task).glob("step-*.jsonl"):
        text = f.read_text(encoding="utf-8")
        assert claude_code.scrub(text) == text, f.name


def test_from_resumes_at_a_step_in_the_session_of_the_step_before(
    tmp_path, monkeypatch
):
    recorded = HERE / "fixtures" / "T-6235"
    shutil.copy(recorded / "step-01.jsonl", tmp_path / "step-01.jsonl")
    asked = []

    def fake(says, cfg, turns, model, session, **_):
        asked.append(session)
        return (recorded / "step-02.jsonl").read_text(encoding="utf-8")

    monkeypatch.setattr(claude_code, "_claude", fake)
    args = [
        "wave",
        "--scenario",
        str(SCENARIO),
        "--mcp-config",
        "c",
        "--out",
        str(tmp_path),
    ]
    assert claude_code.main([*args, "--from", "2"]) == 0
    first = claude_code.load(tmp_path / "step-01.jsonl").session_id
    assert asked[0] == first and len(asked) == len(claude_code.steps(SCENARIO)) - 1

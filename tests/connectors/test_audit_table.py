"""audit_table.py over a ConnectorCalls answer (T-6024): the split, each verdict and the table."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_table

CHECKLIST = yaml.safe_load(
    (HERE / "claude_ai_checklist.yaml").read_text(encoding="utf-8")
)
ROWS = json.loads(
    (HERE / "fixtures" / "claude_ai_calls.json").read_text(encoding="utf-8")
)["items"]


def rows_with(change) -> list[dict]:
    rows = copy.deepcopy(ROWS)
    for row in rows:
        change(row)
    return rows


def test_the_recorded_calls_give_eight_passing_prompts():
    text, ok = audit_table.table(CHECKLIST, ROWS, 60)
    assert ok, text
    lines = text.splitlines()[2:]
    assert [line.split(" | ")[0] for line in lines] == [f"| P{i}" for i in range(1, 9)]
    assert "jc_change_approve (decision door)" in lines[7]


def test_the_split_drops_the_tool_listing_when_the_connector_is_added():
    groups = audit_table.split(ROWS, 60)
    assert len(groups) == 8
    assert groups[0][0]["tool"] == "tools/list", "P1's own tools/list belongs to P1"


def test_a_call_from_another_client_fails_its_prompt():
    code = "https://claude.ai/oauth/claude-code-client-metadata"

    def other(row):
        if row["tool"] == "jc_kpi_compute":
            row["client"] = code

    text, ok = audit_table.table(CHECKLIST, rows_with(other), 60)
    assert not ok
    assert f"| P5 | publish | jc_kpi_compute | - | fail: client {code} |" in text


def test_an_approval_the_connector_got_through_fails_p8():
    def approved(row):
        if row["tool"] == "jc_change_approve":
            row["verdict"] = "allowed"

    text, ok = audit_table.table(CHECKLIST, rows_with(approved), 60)
    assert not ok
    assert "fail: jc_change_approve allowed" in text


def test_a_refused_write_fails_the_prompt_that_needs_it():
    def refused(row):
        if row["tool"] == "jc_change_apply":
            row["verdict"] = "refused: writes"

    text, ok = audit_table.table(CHECKLIST, rows_with(refused), 60)
    assert not ok
    assert "fail: jc_change_apply not allowed" in text
    assert "jc_change_apply (writes)" in text


def test_a_gap_that_splits_wrong_names_the_groups():
    with pytest.raises(SystemExit, match=r"prompt groups for 8 prompts at --gap 1"):
        audit_table.table(CHECKLIST, ROWS, 1)


def test_the_command_line_exits_1_on_a_fail(tmp_path, capsys):
    def refused(row):
        if row["tool"] == "jc_data_query":
            row["verdict"] = "refused: scope"

    calls = tmp_path / "calls.json"
    calls.write_text(json.dumps({"items": rows_with(refused)}), encoding="utf-8")
    assert audit_table.main(["--calls", str(calls)]) == 1
    assert "| P1 | data-read | jc_data_query |" in capsys.readouterr().out


def test_every_checklist_prompt_names_a_segment_scenario_and_tool():
    segments = [p["segment"] for p in CHECKLIST["prompts"]]
    assert len(segments) == 8
    assert {
        "data-read",
        "context",
        "pipeline",
        "access",
        "publish",
        "apps",
        "operate",
    } <= set(segments)
    for p in CHECKLIST["prompts"]:
        assert (
            p["scenario"].startswith("S-") and p["says"] and p["expect"] and p["tools"]
        ), p["id"]

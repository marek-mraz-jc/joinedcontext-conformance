"""The Claude Code transcript reader and table of claude_code.py (T-6025), over two recorded sessions.

fixtures/public-hub-pass.jsonl: Claude Code 2.1.303 asked dev's public hub (https://api.dev.joinedcontext.com/api/mcp)
which Endpoints exist; two list_endpoints calls, an answer. fixtures/public-hub-refused.jsonl: the same hub asked about an
Endpoint slug that does not exist; the hub answered 401, Claude Code reported the server as needing a new sign-in.
"""

from __future__ import annotations

import base64
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import claude_code

FIXTURES = Path(__file__).parent / "fixtures"


def test_a_passing_session_reads_its_calls_turns_and_tokens():
    run = claude_code.load(FIXTURES / "public-hub-pass.jsonl")
    assert [c.tool for c in run.calls] == [
        "mcp__jc__list_endpoints",
        "mcp__jc__list_endpoints",
    ]
    assert [c.refused for c in run.calls] == [False, False]
    assert run.calls[0].arg_bytes == len('{"limit":100}')
    assert run.largest_answer == run.calls[0].answer_bytes
    assert run.largest_answer > 25_000
    assert all(c.ms > 0 for c in run.calls)
    assert (run.turns, run.tools_offered, run.model) == (3, 19, "claude-opus-5-5")
    assert run.start_tokens == 17_470
    assert run.input_tokens == 6 + 35_883 + 49_656
    assert run.output_tokens == 1_164
    assert run.answer.startswith("I found **127 public Endpoints**")
    assert run.hint() == ""


def test_a_refused_call_is_counted_and_named_auth():
    run = claude_code.load(FIXTURES / "public-hub-refused.jsonl")
    assert len(run.calls) == 1
    assert run.calls[0].refused
    assert "sign in again" in run.calls[0].answer_head
    assert run.hint() == "auth"


def test_the_table_rerun_from_saved_files_is_the_same_table(tmp_path):
    shutil.copy(FIXTURES / "public-hub-pass.jsonl", tmp_path / "S-01.jsonl")
    shutil.copy(FIXTURES / "public-hub-refused.jsonl", tmp_path / "S-02.jsonl")
    (tmp_path / "verdicts.yaml").write_text(
        "S-01: {pass: true}\nS-02: {pass: false}\n", encoding="utf-8"
    )
    first = claude_code.table(tmp_path)
    assert first == claude_code.table(tmp_path)
    rows = first.splitlines()
    assert len(rows) == 4
    assert rows[2].startswith("| S-01 | pass | 2 | 0 | 3 | 85545 | 1164 | 17470 |")
    assert rows[3].startswith("| S-02 | fail | 1 | 1 | 2 |")
    assert rows[3].endswith("| auth |")


def test_a_verdict_class_outside_the_list_is_refused(tmp_path):
    shutil.copy(FIXTURES / "public-hub-refused.jsonl", tmp_path / "S-02.jsonl")
    (tmp_path / "verdicts.yaml").write_text(
        "S-02: {pass: false, class: slow}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="class 'slow' is not one of"):
        claude_code.table(tmp_path)


def test_an_unjudged_row_says_so(tmp_path):
    shutil.copy(FIXTURES / "public-hub-pass.jsonl", tmp_path / "S-01.jsonl")
    assert "| S-01 | unjudged |" in claude_code.table(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abc.def-ghi",
        '{"authorization":"Bearer abc"}',
        '{\\"Mcp-Session-Id\\":\\"0f3c9e1a\\"}',
        "mcp-session-id: 0f3c9e1a",
        "token "
        + base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode()
        + ".eyJzdWIiOiJ4In0.c2ln",
    ],
)
def test_scrub_removes_tokens_and_session_ids(raw):
    cleaned = claude_code.scrub(raw)
    for secret in ("abc", "0f3c9e1a", "eyJ"):
        assert secret not in cleaned
    assert "***" in cleaned


def test_the_recorded_fixtures_hold_no_token():
    for path in FIXTURES.glob("*.jsonl"):
        text = path.read_text(encoding="utf-8")
        assert claude_code.scrub(text) == text, path.name


def test_scenarios_keep_the_connector_segments_and_refuse_unknown_ids(tmp_path):
    source = tmp_path / "s.yaml"
    source.write_text(
        "- {id: S-01, segment: data-read, says: a}\n- {id: S-56, segment: ui, says: b}\n",
        encoding="utf-8",
    )
    assert [s["id"] for s in claude_code.scenarios(None, source)] == ["S-01"]
    with pytest.raises(SystemExit, match="unknown scenario id S-99"):
        claude_code.scenarios(["S-99"], source)

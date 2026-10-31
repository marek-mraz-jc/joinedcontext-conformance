"""The Claude Code transcript reader and table of claude_code.py (T-6025), over two recorded sessions.

fixtures/public-hub-pass.jsonl: Claude Code 2.1.303 asked dev's public hub (https://api.dev.joinedcontext.com/api/mcp)
which Endpoints exist; two list_endpoints calls, an answer. fixtures/public-hub-refused.jsonl: the same hub asked about an
Endpoint slug that does not exist; the hub answered 401, Claude Code reported the server as needing a new sign-in.
"""

from __future__ import annotations

import base64
import json
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


def test_a_row_failed_on_an_empty_test_project_names_the_fixture(tmp_path):
    shutil.copy(FIXTURES / "public-hub-refused.jsonl", tmp_path / "S-02.jsonl")
    (tmp_path / "verdicts.yaml").write_text(
        "S-02: {pass: false, class: fixture missing}\n", encoding="utf-8"
    )
    assert claude_code.table(tmp_path).splitlines()[2].endswith("| fixture missing |")


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


def test_the_prompt_adds_the_given_facts_with_the_project_filled_in():
    assert claude_code.prompt({"says": "List it."}, "p") == "List it."
    assert (
        claude_code.prompt(
            {"says": "Retire it.", "given": "Endpoint {project}-counters."},
            "t6025-full",
        )
        == "Retire it.\n\nEndpoint t6025-full-counters."
    )


def test_scrub_keeps_a_schema_member_named_authorization_valid_json():
    text = json.dumps(
        {"properties": {"Authorization": {"type": "object"}, "auth": ["a"]}}
    )
    assert json.loads(claude_code.scrub(text)) == json.loads(text)
    escaped = json.dumps({"text": text})
    assert json.loads(json.loads(claude_code.scrub(escaped))["text"]) == json.loads(
        text
    )


def test_the_verdict_lines_rerun_from_saved_step_files_are_the_same(tmp_path):
    shutil.copy(FIXTURES / "public-hub-pass.jsonl", tmp_path / "step-01.jsonl")
    shutil.copy(FIXTURES / "public-hub-refused.jsonl", tmp_path / "step-02.jsonl")
    (tmp_path / "verdicts.yaml").write_text(
        "step-01: {verdict: ok, proof: 'list_endpoints answered 127 | 2 pages'}\n"
        "step-02: {verdict: defect, proof: hub answered 401}\n",
        encoding="utf-8",
    )
    first = claude_code.verdict(tmp_path)
    assert first == claude_code.verdict(tmp_path)
    rows = first.splitlines()
    assert len(rows) == 4
    pass_run = claude_code.load(tmp_path / "step-01.jsonl")
    assert rows[2] == (
        f"| step-01 | list_endpoints x 2 | {pass_run.duration_ms} "
        f"| {sum(c.answer_bytes for c in pass_run.calls)} | ok "
        "| list_endpoints answered 127 \\| 2 pages |"
    )
    assert pass_run.duration_ms == 11_937
    assert rows[3].startswith("| step-02 | query_entities x 1 | 22544 | 72 |")
    assert rows[3].endswith("| defect | hub answered 401 |")


def test_a_verdict_outside_ok_defect_model_error_is_refused(tmp_path):
    shutil.copy(FIXTURES / "public-hub-refused.jsonl", tmp_path / "step-01.jsonl")
    (tmp_path / "verdicts.yaml").write_text(
        "step-01: {verdict: fine}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="verdict 'fine' is not one of"):
        claude_code.verdict(tmp_path)


def test_a_wave_resumes_one_session_step_after_step(tmp_path, monkeypatch):
    source = tmp_path / "w.yaml"
    source.write_text("steps:\n  - says: one\n  - says: two\n", encoding="utf-8")
    recorded = (FIXTURES / "public-hub-pass.jsonl").read_text(encoding="utf-8")
    seen = []

    def fake(prompt, mcp_config, max_turns, model, resume=None, keep=False):
        seen.append((prompt, resume, keep))
        return recorded

    monkeypatch.setattr(claude_code, "_claude", fake)
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"mcpServers": {"jc": {}}}', encoding="utf-8")
    assert (
        claude_code.main(
            [
                "wave",
                "--scenario",
                str(source),
                "--mcp-config",
                str(cfg),
                "--out",
                str(tmp_path),
            ]
        )
        == 0
    )
    session = claude_code.load(tmp_path / "step-01.jsonl").session_id
    assert session
    assert seen == [("one", None, True), ("two", session, True)]
    assert (tmp_path / "step-02.jsonl").exists()


def test_a_wave_file_without_steps_is_refused(tmp_path):
    source = tmp_path / "w.yaml"
    source.write_text("id: x\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="lists no steps"):
        claude_code.steps(source)


def test_the_committed_wave_scenarios_have_three_to_eight_steps():
    for source in sorted((Path(__file__).parent / "scenarios-wave").glob("*.yaml")):
        assert 3 <= len(claude_code.steps(source)) <= 8, source.name


def test_claude_runs_in_an_empty_directory_outside_any_clone(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code.tempfile, "gettempdir", lambda: str(tmp_path))
    seen = {}

    def fake_run(argv, **kw):
        seen["cwd"] = kw["cwd"]
        return claude_code.subprocess.CompletedProcess(
            argv, 0, stdout='{"type":"result"}\n', stderr=""
        )

    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    claude_code._claude("hi", None, 1, None)
    assert seen["cwd"] == tmp_path / "claude-code-connector-run"
    assert list(seen["cwd"].iterdir()) == []
    (seen["cwd"] / "stray").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        claude_code._claude("hi", None, 1, None)


def test_scrub_keeps_an_escape_after_an_authorization_key_valid_json():
    # A YAML manifest in a model answer: `authorization:` then an escaped newline (T-6251 step 4).
    text = json.dumps({"text": "http:\n  authorization:\n    headerRef: {name: a}"})
    assert claude_code.scrub(text) == text

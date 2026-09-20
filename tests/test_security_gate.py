"""The production security gate over the attack-vector register (T-1722, OPS-27, TS-19).

Each case writes a small register page and a small repository tree, so the assertions are about
the gate and not about how many vectors happen to be open on the day they run. The fixtures reuse
`tests/fixtures/compliance`, whose ZZ/YY families no requirement page of the platform defines.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "security_gate.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compliance"


def load_gate():
    specification = importlib.util.spec_from_file_location("security_gate", SCRIPT)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules["security_gate"] = module
    specification.loader.exec_module(module)
    return module


gate = load_gate()

PROVEN = ("joinedcontext-platform/crates/jc-core/tests/verdict_tests.rs::"
          "a_write_without_a_verdict_is_refused")
SWITCHED_OFF = ("joinedcontext-platform/crates/jc-core/tests/verdict_tests.rs::"
                "an_ignored_case_without_a_task")


def page(rows: list[str], heading: str = gate.HEADING) -> str:
    return "\n".join([
        "---", "sidebar_position: 7", "title: Security Testing", "---", "",
        "# Security Testing", "", "A lead paragraph.", "",
        heading, "", "Some prose about the gate.", "",
        "| Surface | Vector | Task | Requirements | Test | State |",
        "|---|---|---|---|---|---|",
        *rows,
        "", "## Related", "", "- [a](a.md) — why.",
    ])


def row(vector: str, task: str = "T-1672", requirements: str = "ZZ-01",
        test: str = "", state: str = "open") -> str:
    return f"| edge | {vector} | {task} | {requirements} | {test} | {state} |"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    def place(fixture: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8")

    place("requirements.md", tmp_path / "docs" / "Requirements" / "platform.md")
    place("verdict_tests.rs.txt",
          tmp_path / "joinedcontext-platform" / "crates" / "jc-core" / "tests" / "verdict_tests.rs")
    return tmp_path


def index_of(workspace: Path) -> dict:
    return gate.compliance.build_index(
        workspace / "docs",
        {"joinedcontext-platform": workspace / "joinedcontext-platform"},
    )


def verdict(workspace: Path, rows: list[str], *, open_is_red: bool = True):
    register = workspace / "docs" / "Testing" / "06-security-tests.md"
    register.parent.mkdir(parents=True, exist_ok=True)
    register.write_text(page(rows), encoding="utf-8")
    parsed, problems = gate.parse_table(register.read_text(encoding="utf-8"))
    failures, still_open = gate.check_rows(parsed, index_of(workspace))
    return problems + failures, still_open


def test_a_complete_row_passes_the_gate(workspace: Path):
    """OPS-27 - a vector with a named test that exists and runs is proven and nothing else."""
    failures, still_open = verdict(workspace, [row("a header from outside", test=f"`{PROVEN}`",
                                                   state="proven")])
    assert failures == []
    assert still_open == []


def test_a_row_naming_a_test_that_does_not_exist_fails_the_gate(workspace: Path):
    """OPS-27 - a test renamed away must turn the gate red, never stop proving quietly."""
    missing = "joinedcontext-platform/crates/jc-core/tests/verdict_tests.rs::a_test_nobody_wrote"
    failures, _ = verdict(workspace, [row("a header from outside", test=f"`{missing}`",
                                          state="proven")])
    assert any("which no test of the five repositories is" in failure for failure in failures)


def test_a_row_naming_a_switched_off_test_fails_the_gate(workspace: Path):
    failures, _ = verdict(workspace, [row("a header from outside", test=f"`{SWITCHED_OFF}`",
                                          state="proven")])
    assert any("is switched off" in failure for failure in failures)


def test_a_proven_row_without_a_test_fails_the_gate(workspace: Path):
    failures, _ = verdict(workspace, [row("a header from outside", state="proven")])
    assert any("is proven by no named test" in failure for failure in failures)


def test_an_open_row_that_names_a_test_fails_the_gate(workspace: Path):
    """The two columns cannot disagree: a row with a test that replays the attack is proven."""
    failures, _ = verdict(workspace, [row("a header from outside", test=f"`{PROVEN}`")])
    assert any("names a test anyway" in failure for failure in failures)


def test_an_open_row_is_counted_and_keeps_the_gate_red(workspace: Path):
    failures, still_open = verdict(workspace, [
        row("a header from outside"),
        row("a route without its plugin", task="T-1673", test=f"`{PROVEN}`", state="proven"),
    ])
    assert failures == []
    assert [open_row.vector for open_row in still_open] == ["a header from outside"]


def test_a_row_naming_a_requirement_that_does_not_exist_fails_the_gate(workspace: Path):
    failures, _ = verdict(workspace, [row("a header from outside", requirements="ZZ-99")])
    assert any("which docs/Requirements does not define" in failure for failure in failures)


def test_a_row_naming_no_requirement_fails_the_gate(workspace: Path):
    failures, _ = verdict(workspace, [row("a header from outside", requirements="")])
    assert any("names no requirement it proves" in failure for failure in failures)


def test_a_row_without_a_task_or_with_an_unknown_state_fails_the_gate(workspace: Path):
    failures, _ = verdict(workspace, [row("a header from outside", task="nobody")])
    assert any("must be one T-xxxx id" in failure for failure in failures)
    failures, _ = verdict(workspace, [row("a header from outside", state="probably")])
    assert any("must be one of" in failure for failure in failures)


def test_the_same_vector_twice_fails_the_gate(workspace: Path):
    """Two rows for one attack let one of them be ticked and the other forgotten."""
    failures, _ = verdict(workspace, [row("a header from outside"),
                                      row("a header from outside", task="T-1673")])
    assert any("is already a row at line" in failure for failure in failures)


def test_a_row_with_the_wrong_number_of_cells_is_reported_and_not_dropped(workspace: Path):
    failures, _ = verdict(workspace, ["| edge | a header from outside | T-1672 | open |"])
    assert any("4 cells, 6 expected" in failure for failure in failures)


def test_a_page_without_the_register_fails_the_gate(tmp_path: Path):
    rows, problems = gate.parse_table("# Security Testing\n\nNo register here.\n")
    assert rows == []
    assert any("carries no" in problem for problem in problems)


def test_an_empty_register_fails_the_gate(tmp_path: Path):
    rows, problems = gate.parse_table(page([]))
    assert rows == []
    assert any("no row" in problem for problem in problems)


def test_the_committed_register_parses_and_every_row_is_well_formed():
    """The register this repository gates on is read by the gate that gates on it."""
    docs = Path(__file__).resolve().parents[2] / "docs"
    register = docs / "Testing" / "06-security-tests.md"
    if not register.is_file():
        pytest.skip(f"{register} is not checked out beside this repository")
    rows, problems = gate.parse_table(register.read_text(encoding="utf-8"))
    assert problems == []
    assert len(rows) >= 50, "the register lost rows: every attack vector keeps its own"
    assert {row_.state for row_ in rows} <= gate.STATES


def test_the_cli_is_red_while_a_vector_is_open_and_green_when_told_to_ignore_open_rows(
        workspace: Path, capsys):
    register = workspace / "docs" / "Testing" / "06-security-tests.md"
    register.parent.mkdir(parents=True, exist_ok=True)
    register.write_text(page([row("a header from outside")]), encoding="utf-8")
    arguments = ["--page", str(register), "--docs", str(workspace / "docs"),
                 "--repo", f"joinedcontext-platform={workspace / 'joinedcontext-platform'}"]
    assert gate.main(arguments) == 1
    assert gate.main(arguments + ["--no-open-is-red"]) == 0
    assert "1 open" in capsys.readouterr().out

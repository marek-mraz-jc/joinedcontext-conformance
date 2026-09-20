"""The compliance index, its gate and its report (T-1725, TS-19, OPS-27).

Every case builds a small docs tree and a small repository tree from `tests/fixtures/compliance`,
so the assertions are about the script and not about the state of the platform on the day they
run. One fixture per language the platform tests in, because each one spells a citation
differently.

The fixtures live in files with a `.txt` suffix and name a ZZ/YY family that no requirement page
defines: a fixture written inline here would be read by the scanner as a real test of this
repository, and a fixture id borrowed from a real family would be read as a real citation.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compliance.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compliance"


def load_compliance():
    specification = importlib.util.spec_from_file_location("compliance", SCRIPT)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules["compliance"] = module
    specification.loader.exec_module(module)
    return module


compliance = load_compliance()


def place(fixture: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text((FIXTURES / fixture).read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    place("requirements.md", tmp_path / "docs" / "Requirements" / "platform.md")
    place("verdict_tests.rs.txt",
          tmp_path / "joinedcontext-platform" / "crates" / "jc-core" / "tests" / "verdict_tests.rs")
    place("form.test.tsx.txt",
          tmp_path / "joinedcontext-portal" / "ui" / "tests" / "form.test.tsx")
    place("test_portal_mcp.py.txt",
          tmp_path / "joinedcontext-conformance" / "tests" / "mcp" / "test_portal_mcp.py")
    place("smoke.robot.txt",
          tmp_path / "joinedcontext-conformance" / "tests" / "etsi" / "smoke.robot")
    # Production code, not a test: it is what makes ZZ-03 `built` rather than `open` (T-2142).
    place("handler.rs.txt",
          tmp_path / "joinedcontext-platform" / "crates" / "jc-core" / "src" / "handler.rs")
    return tmp_path


def index_of(workspace: Path) -> dict:
    repos = {
        "joinedcontext-platform": workspace / "joinedcontext-platform",
        "joinedcontext-portal": workspace / "joinedcontext-portal",
        "joinedcontext-conformance": workspace / "joinedcontext-conformance",
    }
    return compliance.build_index(workspace / "docs", repos)


def names_for(index: dict, requirement: str) -> set[str]:
    return {index["tests"][position]["name"] for position in index["proves"].get(requirement, [])}


def case(index: dict, name: str) -> dict:
    matches = [test for test in index["tests"] if test["name"] == name]
    assert matches, f"{name} was not indexed at all"
    return matches[0]


def test_every_language_puts_its_citing_test_in_the_index(workspace: Path):
    """TS-19 - one test per language, each cited where that language writes its citation."""
    index = index_of(workspace)
    assert names_for(index, "ZZ-01") == {
        "a_write_without_a_verdict_is_refused",
        "names the field",
        "test_zz01_a_write_without_a_verdict_is_refused",
        "A Refused Write Names Its Field",
        "names the second field",
    }


def test_a_test_that_names_nothing_inherits_its_file(workspace: Path):
    """TS-19 - a file header citation covers the cases that cite nothing themselves."""
    index = index_of(workspace)
    assert names_for(index, "ZZ-02") == {
        "the_field_is_named",
        "an_ignored_case_without_a_task",
        "an_ignored_case_with_a_task",
        "test_the_file_requirement_is_inherited",
        "An Inherited Case",
    }
    # the vitest file's own header names YY-01, so its uncited case inherits that one
    assert names_for(index, "YY-01") == {"inherits the file's requirement"}


def test_a_test_that_names_its_own_requirement_does_not_inherit_the_file(workspace: Path):
    """TS-19 - otherwise a file header would claim every case in it and read as coverage."""
    index = index_of(workspace)
    assert case(index, "a_write_without_a_verdict_is_refused")["requirements"] == ["ZZ-01"]
    assert case(index, "names the field")["requirements"] == ["ZZ-01"]


def test_a_function_that_is_not_a_test_is_not_indexed(workspace: Path):
    index = index_of(workspace)
    assert all(test["name"] != "not_a_test" for test in index["tests"])


def test_each_test_carries_the_lane_that_runs_it(workspace: Path):
    index = index_of(workspace)
    assert case(index, "a_write_without_a_verdict_is_refused")["lane"] == "ci-full"
    assert case(index, "names the field")["lane"] == "fast ci"
    assert case(index, "test_zz01_a_write_without_a_verdict_is_refused")["lane"] == "conformance"
    assert case(index, "A Refused Write Names Its Field")["lane"] == "live on dev"
    assert all(test["lane"] != "unknown" for test in index["tests"])


def test_a_requirement_with_a_test_is_tested(workspace: Path):
    """TS-19, T-2142 - a test names ZZ-01, so a regression in it turns a lane red."""
    index = index_of(workspace)
    assert compliance.state_of("ZZ-01", index) == "tested"


def test_a_requirement_only_the_code_names_is_built(workspace: Path):
    """TS-19, T-2142 - ZZ-05 is claimed by a handler and by no test: written down, unchecked."""
    index = index_of(workspace)
    assert compliance.state_of("ZZ-05", index) == "built"
    assert index["implements"]["ZZ-05"] == ["joinedcontext-platform/crates/jc-core/src/handler.rs"]


def test_a_requirement_nothing_names_is_open(workspace: Path):
    """TS-19, T-2142 - ZZ-04 is a sentence in a document and nothing else."""
    index = index_of(workspace)
    assert compliance.state_of("ZZ-04", index) == "open"
    assert "ZZ-04" not in index["proves"]
    assert "ZZ-04" not in index["implements"]


def test_a_test_file_is_never_read_as_the_code_that_implements_it(workspace: Path):
    """TS-19, T-2142 - otherwise every tested requirement would also read as built, and the
    three states would collapse into one."""
    index = index_of(workspace)
    assert "ZZ-01" not in index["implements"]
    assert "YY-01" not in index["implements"]


def test_the_matrix_prints_one_of_the_three_states_for_every_requirement(workspace: Path):
    """TS-19, T-2142 - the page a reader sees is the join the gate holds."""
    index = index_of(workspace)
    page = compliance.render_matrix(index)
    for identifier, state in [("ZZ-01", "tested"), ("ZZ-05", "built"), ("ZZ-04", "open")]:
        row = next(line for line in page.splitlines() if line.startswith(f"| **{identifier}**"))
        assert f"| {state} |" in row, row
    assert "crates/jc-core/src/handler.rs" in page, "a built requirement names what claims it"


def test_an_unproven_security_requirement_fails_the_check(workspace: Path):
    """TS-19 - ZZ-04 is tagged [S] and no test names it at all."""
    index = index_of(workspace)
    failures = compliance.check_index(index, {"uncited_requirements": 10})
    assert any(failure.startswith("ZZ-04 [S]") for failure in failures)
    assert not any(failure.startswith("ZZ-01") for failure in failures)
    assert not any(failure.startswith("ZZ-02") for failure in failures)


def test_a_citation_of_an_id_that_does_not_exist_fails_the_check(workspace: Path):
    place("typo.test.tsx.txt", workspace / "joinedcontext-portal" / "ui" / "tests" / "typo.test.tsx")
    index = index_of(workspace)
    assert "ZZ-99" in index["unknown_citations"]
    failures = compliance.check_index(index, {"uncited_requirements": 10})
    assert any("ZZ-99" in failure and "no requirement of that id exists" in failure for failure in failures)


def test_a_switched_off_test_fails_the_check_only_without_a_task_id(workspace: Path):
    index = index_of(workspace)
    failures = compliance.check_index(index, {"uncited_requirements": 10})
    switched_off = [failure for failure in failures if "switched off" in failure]
    assert any("an_ignored_case_without_a_task" in failure for failure in switched_off)
    assert not any("an_ignored_case_with_a_task" in failure for failure in switched_off)


def test_a_conditional_skip_is_not_a_switched_off_test(workspace: Path):
    """A `skipif` binds a case to an environment; it does not disable it, and the report says
    `not run` for it rather than the gate calling it a violation."""
    place("test_conditional.py.txt",
          workspace / "joinedcontext-conformance" / "tests" / "mcp" / "test_conditional.py")
    index = index_of(workspace)
    assert case(index, "test_zz01_only_with_a_broker")["skip"] == "conditional"
    failures = compliance.check_index(index, {"uncited_requirements": 10})
    assert not any("test_zz01_only_with_a_broker" in failure for failure in failures)


def test_coverage_that_falls_below_the_committed_baseline_fails_the_check(workspace: Path):
    index = index_of(workspace)
    uncited = len([r for r in index["requirements"] if r["id"] not in index["proves"]])
    assert not any("baseline" in failure
                   for failure in compliance.check_index(index, {"uncited_requirements": uncited}))
    failures = compliance.check_index(index, {"uncited_requirements": uncited - 1})
    assert any("above the committed baseline" in failure for failure in failures)


def test_a_baseline_without_a_number_fails_the_check(workspace: Path):
    failures = compliance.check_index(index_of(workspace), {})
    assert any("carries no integer" in failure for failure in failures)


def junit(path: Path, cases: list[tuple[str, str, str | None]]) -> Path:
    """A JUnit file with `(classname, name, failure message or None)` per case."""
    body = []
    for classname, name, failure in cases:
        inner = f"<failure message=\"{failure}\"/>" if failure else ""
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    path.write_text(
        f'<?xml version="1.0"?><testsuite name="fixture" tests="{len(cases)}">'
        + "".join(body)
        + "</testsuite>",
        encoding="utf-8",
    )
    return path


def test_a_requirement_is_green_only_when_every_test_citing_it_passed(workspace: Path, tmp_path: Path):
    """TS-19 - the done-when of T-1725: one failing citing test is enough to take the green away."""
    index = index_of(workspace)
    reports = tmp_path / "junit"
    reports.mkdir()
    everything = [
        ("jc_core::verdict_tests", "a_write_without_a_verdict_is_refused", None),
        ("ui/tests/form.test.tsx", "the refusal (ZZ-01) > names the field", None),
        ("tests.mcp.test_portal_mcp", "test_zz01_a_write_without_a_verdict_is_refused", None),
        ("etsi.Smoke", "A Refused Write Names Its Field", None),
        ("ui/tests/form.test.tsx", "the refusal (ZZ-01) > names the second field", None),
    ]
    junit(reports / "all.xml", everything)
    green = compliance.build_report(index, [reports])
    assert green["requirements"]["ZZ-01"]["state"] == "passed"

    junit(reports / "all.xml", everything[:-1] + [
        ("ui/tests/form.test.tsx", "the refusal (ZZ-01) > names the second field", "expected 1 to be 2"),
    ])
    red = compliance.build_report(index, [reports])
    assert red["requirements"]["ZZ-01"]["state"] == "failed"
    assert red["totals"]["failed"] >= 1


def test_a_run_that_carries_only_some_lanes_leaves_the_requirement_partly_run(workspace: Path, tmp_path: Path):
    index = index_of(workspace)
    reports = tmp_path / "junit"
    reports.mkdir()
    junit(reports / "one.xml", [("jc_core::verdict_tests", "a_write_without_a_verdict_is_refused", None)])
    report = compliance.build_report(index, [reports])
    assert report["requirements"]["ZZ-01"]["state"] == "partial"
    assert report["requirements"]["YY-01"]["state"] == "unproven"
    assert report["requirements"]["ZZ-03"]["state"] == "unproven"


def test_a_requirement_no_test_names_is_reported_as_having_none(workspace: Path, tmp_path: Path):
    index = index_of(workspace)
    reports = tmp_path / "junit"
    reports.mkdir()
    junit(reports / "one.xml", [("x", "nothing_we_know", None)])
    report = compliance.build_report(index, [reports])
    uncited = [identifier for identifier, verdict in report["requirements"].items()
               if verdict["state"] == "uncited"]
    # The two fixture requirements no test names: one claimed by code, one by nothing.
    assert uncited == ["ZZ-04", "ZZ-05"]
    assert report["junit_cases"] == 1
    assert report["matched_tests"] == 0


def test_the_report_names_its_verdict_in_words_and_not_only_in_colour(workspace: Path, tmp_path: Path):
    """A greyscale print and a screen reader read the same verdict (UI-13)."""
    index = index_of(workspace)
    reports = tmp_path / "junit"
    reports.mkdir()
    junit(reports / "all.xml", [
        ("jc_core::verdict_tests", "a_write_without_a_verdict_is_refused", "boom"),
    ])
    page = compliance.render_report_html(compliance.build_report(index, [reports]))
    assert "<title>joinedcontext platform compliance report</title>" in page
    assert ">failed<" in page
    assert 'scope="row"' in page and 'scope="col"' in page
    assert "ZZ-01" in page


def test_a_corrupt_junit_file_stops_the_report_instead_of_reporting_green(tmp_path: Path):
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuite>not closed", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        compliance.parse_junit([broken])
    assert "not valid JUnit XML" in str(raised.value)


def test_a_missing_junit_path_stops_the_report(tmp_path: Path):
    with pytest.raises(SystemExit) as raised:
        compliance.parse_junit([tmp_path / "nowhere"])
    assert "no such JUnit file or directory" in str(raised.value)


def test_the_matrix_page_follows_the_documentation_style(workspace: Path):
    """docs/STYLE.md: front matter, one H1, numbered H2 sections and a closing `## Related`."""
    page = compliance.render_matrix(index_of(workspace)).splitlines()
    assert page[0] == "---"
    assert 'title: "Requirement Compliance Matrix"' in page
    assert len([line for line in page if line.startswith("# ")]) == 1
    assert [line for line in page if line.startswith("## ")][:2] == [
        "## 1. How to read this page", "## 2. Coverage by family"]
    assert "## Related" in page
    assert any("**ZZ-01**" in line and "[S]" in line for line in page)
    assert any("compliance-matrix" not in line and "a_write_without_a_verdict_is_refused" in line
               for line in page)


def test_the_index_records_which_commit_of_each_repository_it_read(workspace: Path):
    index = index_of(workspace)
    assert set(index["commits"]) == {
        "joinedcontext-platform", "joinedcontext-portal", "joinedcontext-conformance"}


def test_the_cli_writes_the_index_the_matrix_and_the_baseline(workspace: Path, tmp_path: Path):
    out = tmp_path / "out"
    code = compliance.main([
        "index",
        "--repo", f"joinedcontext-platform={workspace / 'joinedcontext-platform'}",
        "--repo", f"joinedcontext-portal={workspace / 'joinedcontext-portal'}",
        "--repo", f"joinedcontext-conformance={workspace / 'joinedcontext-conformance'}",
        "--docs", str(workspace / "docs"),
        "--index", str(out / "index.json"),
        "--matrix", str(out / "compliance-matrix.md"),
        "--baseline", str(out / "baseline.json"),
        "--record-baseline",
    ])
    assert code == 0
    written = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert written["proves"]["ZZ-01"]
    assert json.loads((out / "baseline.json").read_text(encoding="utf-8"))["uncited_requirements"] == 2
    assert (out / "compliance-matrix.md").read_text(encoding="utf-8").startswith("---")

    # `check` always rescans, so it needs the same fixture tree the index was built from. Without
    # `--docs` and `--repo` it falls back to the tree beside the clone: green on a workstation that
    # has a docs checkout there (and gating on the real repository rather than on this fixture),
    # `SystemExit: …/docs/Requirements is not a directory` on a CI runner that has none.
    assert compliance.main([
        "check",
        "--repo", f"joinedcontext-platform={workspace / 'joinedcontext-platform'}",
        "--repo", f"joinedcontext-portal={workspace / 'joinedcontext-portal'}",
        "--repo", f"joinedcontext-conformance={workspace / 'joinedcontext-conformance'}",
        "--docs", str(workspace / "docs"),
        "--index", str(out / "index.json"), "--baseline", str(out / "baseline.json"),
    ]) == 1, "the fixture has an unproven [S] requirement, so the gate must be red"


def test_an_unnamed_repository_directory_is_refused(tmp_path: Path):
    with pytest.raises(SystemExit) as raised:
        compliance.resolve_repos(["joinedcontext-portal=/does/not/exist"], tmp_path)
    assert "is not a directory" in str(raised.value)
    with pytest.raises(SystemExit) as raised:
        compliance.resolve_repos(["joinedcontext-portal"], tmp_path)
    assert "name=path" in str(raised.value)

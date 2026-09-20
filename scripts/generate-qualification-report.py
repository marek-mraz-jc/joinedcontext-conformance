#!/usr/bin/env python3
"""Conformance qualification report aggregator (T-0089, TS-05, TS-07, TS-08, TS-13).

Aggregates test suite outcomes (Robot Framework, TestNG, JUnit, axe accessibility, and DSP)
into a single markdown qualification report satisfying docs/STYLE.md.

    generate-qualification-report.py --reports reports/ [--output report.md] [--for <ref>] [--allow-missing <suites>]
    generate-qualification-report.py --selftest
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Conformance class <-> Robot tag mapping for NGSI-LD (EP-30, TS-19)
CLASS_TO_TAG: dict[str, str] = {
    "core": "smoke",
    "temporal": "temporal",
}
TAG_TO_CLASS: dict[str, str] = {v: k for k, v in CLASS_TO_TAG.items()}


@dataclass(frozen=True)
class SuiteResult:
    suite: str
    passed: int
    failed: int
    skipped: int
    detail: str
    findings: tuple[dict, ...] = ()


def parse_robot(path: Path) -> SuiteResult:
    suite_name = path.parent.name
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        stat = root.find(".//statistics/total/stat")
        if stat is None:
            stat = root.find(".//total/stat")
        if stat is None:
            stat = root.find(".//stat")
        if stat is not None:
            passed = int(stat.attrib.get("pass", 0))
            failed = int(stat.attrib.get("fail", 0))
            skipped = int(stat.attrib.get("skip", 0))
            detail = f"{passed} passed, {failed} failed, {skipped} skipped"
            return SuiteResult(suite=suite_name, passed=passed, failed=failed, skipped=skipped, detail=detail)
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: missing <stat> element")
    except Exception as exc:
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: parse error: {exc}")


def parse_testng(path: Path) -> SuiteResult:
    suite_name = path.parent.name
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        elem = root if root.tag == "testng-results" else root.find(".//testng-results")
        if elem is not None:
            passed = int(elem.attrib.get("passed", 0))
            failed = int(elem.attrib.get("failed", 0))
            skipped = int(elem.attrib.get("skipped", 0))
            detail = f"{passed} passed, {failed} failed, {skipped} skipped"
            return SuiteResult(suite=suite_name, passed=passed, failed=failed, skipped=skipped, detail=detail)
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: missing <testng-results>")
    except Exception as exc:
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: parse error: {exc}")


def parse_junit(path: Path) -> SuiteResult:
    suite_name = path.parent.name
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites:
            cases = root.findall(".//testcase")
            if not cases:
                return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: no testsuite or testcase found")
            total = len(cases)
            failed = len(root.findall(".//failure")) + len(root.findall(".//error"))
            skipped = len(root.findall(".//skipped"))
            passed = max(0, total - failed - skipped)
        else:
            total = sum(int(s.attrib.get("tests", 0)) for s in suites)
            failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
            errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
            skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
            failed = failures + errors
            passed = max(0, total - failed - skipped)
        detail = f"{passed} passed, {failed} failed, {skipped} skipped"
        return SuiteResult(suite=suite_name, passed=passed, failed=failed, skipped=skipped, detail=detail)
    except Exception as exc:
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: parse error: {exc}")


def parse_axe(directory: Path) -> SuiteResult:
    suite_name = directory.name
    json_files = sorted(directory.glob("a11y/*.json"))
    if not json_files:
        json_files = sorted(directory.glob("*.json"))
    if not json_files:
        return SuiteResult(suite=suite_name, passed=0, failed=0, skipped=0, detail="not run")

    passed = 0
    failed = 0
    view_findings: list[dict] = []

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            view = data.get("view", jf.stem)
            violations = data.get("violations", [])
            if not violations:
                passed += 1
                view_findings.append({"view": view, "passed": True, "violations": []})
            else:
                failed += 1
                view_findings.append({"view": view, "passed": False, "violations": violations})
        except Exception as exc:
            failed += 1
            view_findings.append({
                "view": jf.stem,
                "passed": False,
                "violations": [{"id": "unreadable-report", "impact": "critical", "help": f"parse error: {exc}", "nodes": []}],
            })

    total_views = passed + failed
    detail = f"{passed} passed, {failed} failed across {total_views} view(s)"
    return SuiteResult(
        suite=suite_name,
        passed=passed,
        failed=failed,
        skipped=0,
        detail=detail,
        findings=tuple(view_findings),
    )


def extract_robot_passing_tags(xml_path: Path) -> set[str]:
    passing_tags = set()
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for test in root.iter("test"):
            status_elem = test.find("status")
            if status_elem is not None and status_elem.get("status") == "PASS":
                for tag_elem in test.iter("tag"):
                    if tag_elem.text:
                        passing_tags.add(tag_elem.text.strip())
    except Exception as exc:
        raise ValueError(f"failed to parse robot output {xml_path}: {exc}") from exc
    return passing_tags


def check_claims(claims_path: Path, output_xml: Path) -> list[str]:
    problems: list[str] = []
    if not claims_path.is_file():
        return [f"claims file not found: {claims_path}"]
    if not output_xml.is_file():
        return [f"robot output file not found: {output_xml}"]

    try:
        claims_data = json.loads(claims_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"malformed claims file {claims_path}: {exc}"]

    ngsild_claims = claims_data.get("ngsi-ld", [])
    claimed_classes = set(ngsild_claims)

    try:
        passing_tags = extract_robot_passing_tags(output_xml)
    except Exception as exc:
        return [str(exc)]

    # 1. Every claimed class MUST have a passing suite in output.xml
    for cls in ngsild_claims:
        expected_tag = CLASS_TO_TAG.get(cls)
        if not expected_tag:
            problems.append(f"unrecognized claimed class '{cls}' has no known robot tag mapping")
            continue
        if expected_tag not in passing_tags:
            problems.append(f"claimed conformance class '{cls}' has no passing suite in {output_xml.name}")

    # 2. Every passing tag that maps to a conformance class MUST be claimed
    for tag in sorted(passing_tags):
        mapped_cls = TAG_TO_CLASS.get(tag)
        if mapped_cls and mapped_cls not in claimed_classes:
            problems.append(f"robot suite tag '{tag}' passed for class '{mapped_cls}' nobody claims")

    return problems


def parse_verdict(path: Path, suite_name: str) -> SuiteResult:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            exit_code_val = data.get("exit_code", data.get("exitCode"))
            if exit_code_val is None and "problems" in data:
                exit_code_val = 1 if data["problems"] else 0
            if exit_code_val is None:
                exit_code_val = 0 if data.get("status") in ("pass", "passed", "ok", 0) else 1
            passed = int(data.get("passed", 1 if exit_code_val == 0 else 0))
            failed = int(data.get("failed", 0 if exit_code_val == 0 else 1))
            skipped = int(data.get("skipped", 0))
            detail = data.get("detail", "TCK passed" if exit_code_val == 0 else "TCK failed")
            return SuiteResult(suite=suite_name, passed=passed, failed=failed, skipped=skipped, detail=detail)
        elif isinstance(data, int):
            passed = 1 if data == 0 else 0
            failed = 0 if data == 0 else 1
            return SuiteResult(suite=suite_name, passed=passed, failed=failed, skipped=0, detail="TCK passed" if data == 0 else "TCK failed")
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: unrecognized structure")
    except Exception as exc:
        return SuiteResult(suite=suite_name, passed=0, failed=1, skipped=0, detail=f"{path.name}: parse error: {exc}")


def expected_suites(repo_root: Path | None = None) -> list[str]:
    """The suites this repository owns, read from the tree so the list cannot drift.

    Without it an empty reports/ tree renders a report with no rows and exits 0 — a
    qualification gate that passes because nothing ran. A suite the repository owns
    that produced no report is "not run", which is a finding.
    """
    root = repo_root or Path(__file__).resolve().parent.parent
    tests = root / "tests"
    if not tests.is_dir():
        return []
    return sorted(d.name for d in tests.iterdir() if d.is_dir() and (d / "run.sh").is_file())


def collect(reports_root: Path, expected: list[str] | tuple[str, ...] = ()) -> list[SuiteResult]:
    if not reports_root.exists() or not reports_root.is_dir():
        return [
            SuiteResult(suite=name, passed=0, failed=0, skipped=0, detail="not run")
            for name in sorted(expected)
        ]

    suite_dirs = sorted([d for d in reports_root.iterdir() if d.is_dir()])
    results: list[SuiteResult] = []

    for sdir in suite_dirs:
        suite_name = sdir.name
        if (sdir / "output.xml").is_file():
            results.append(parse_robot(sdir / "output.xml"))
            continue
        if (sdir / "testng-results.xml").is_file():
            results.append(parse_testng(sdir / "testng-results.xml"))
            continue
        if (sdir / "verdict.json").is_file():
            results.append(parse_verdict(sdir / "verdict.json", suite_name))
            continue
        if suite_name == "dsp" or (sdir / "tck.log").is_file():
            results.append(SuiteResult(suite=suite_name, passed=0, failed=0, skipped=0, detail="not run"))
            continue
        a11y_files = list(sdir.glob("a11y/*.json")) or (list(sdir.glob("*.json")) if "a11y" in suite_name else [])
        if a11y_files:
            results.append(parse_axe(sdir))
            continue
        xml_files = sorted([f for f in sdir.glob("*.xml") if f.name not in ("output.xml", "testng-results.xml")])
        if xml_files:
            if len(xml_files) == 1:
                results.append(parse_junit(xml_files[0]))
            else:
                sub_results = [parse_junit(f) for f in xml_files]
                passed = sum(r.passed for r in sub_results)
                failed = sum(r.failed for r in sub_results)
                skipped = sum(r.skipped for r in sub_results)
                detail = f"{passed} passed, {failed} failed, {skipped} skipped across {len(xml_files)} files"
                results.append(SuiteResult(suite=suite_name, passed=passed, failed=failed, skipped=skipped, detail=detail))
            continue
        results.append(SuiteResult(suite=suite_name, passed=0, failed=0, skipped=0, detail="not run"))

    seen = {r.suite for r in results}
    for name in sorted(expected):
        if name not in seen:
            results.append(SuiteResult(suite=name, passed=0, failed=0, skipped=0, detail="not run"))

    return sorted(results, key=lambda r: r.suite)


def badge_for(result: SuiteResult) -> tuple[str, str]:
    if result.detail == "not run" or (result.passed == 0 and result.failed == 0 and result.skipped == 0):
        return "n/a", "not run"
    total = result.passed + result.failed
    if total == 0:
        return "100.0%", "green"
    rate = (result.passed / total) * 100.0
    rate_str = f"{rate:.1f}%"
    if rate == 100.0:
        return rate_str, "green"
    elif rate > 95.0:
        return rate_str, "yellow"
    return rate_str, "red"


def render(results: list[SuiteResult], *, generated_for: str) -> str:
    lines = [
        "---",
        "sidebar_position: 1",
        "title: Conformance Qualification Report",
        "description: Automated test suite results and conformance qualification report for joinedcontext platform.",
        "---",
        "",
        "# Conformance Qualification Report",
        "",
        f"Conformance qualification report for joinedcontext platform generated for {generated_for}. Release engineers and auditors use this report to verify automated suite results and system conformance.",
        "",
        "## 1. Summary",
        "",
        "| Suite | Passed | Failed | Skipped | Pass Rate | Badge |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        rate_str, badge = badge_for(r)
        lines.append(f"| {r.suite} | {r.passed} | {r.failed} | {r.skipped} | {rate_str} | {badge} |")

    lines.extend([
        "",
        "## 2. Suite details",
        "",
    ])

    for r in results:
        lines.extend([
            f"### {r.suite}",
            "",
            r.detail,
            "",
        ])

    lines.extend([
        "## 3. Accessibility findings",
        "",
    ])

    all_findings: list[dict] = []
    for r in results:
        all_findings.extend(r.findings)

    if not all_findings:
        lines.extend(["No accessibility findings recorded.", ""])
    else:
        for finding in all_findings:
            view = finding.get("view", "unknown")
            lines.extend([f"### {view}", ""])
            if finding.get("passed"):
                lines.extend(["No accessibility violations found.", ""])
            else:
                for v in finding.get("violations", []):
                    v_id = v.get("id", "unknown")
                    impact = v.get("impact", "unknown")
                    help_text = v.get("help") or v.get("description", "")
                    nodes = len(v.get("nodes", []))
                    lines.append(f"- **{v_id}** ({impact}): {help_text} ({nodes} element(s) affected)")
                lines.append("")

    lines.extend([
        "## Related",
        "",
        "- [Testing strategy](../Testing/01-testing-strategy.md) — test families and qualification gates.",
        "- [Frontend and e2e tests](../Testing/03-frontend-and-e2e-tests.md) — accessibility verification under TS-13.",
        "- [Requirements index](../Requirements/00-index.md) — system requirements and conformance criteria.",
        "",
    ])

    return "\n".join(lines)


def overall(results: list[SuiteResult]) -> tuple[int, int, int]:
    return (
        sum(r.passed for r in results),
        sum(r.failed for r in results),
        sum(r.skipped for r in results),
    )


def exit_code(results: list[SuiteResult], allow_missing: set[str] | None = None) -> int:
    allow = allow_missing or set()
    for r in results:
        if r.failed > 0:
            return 1
        if r.detail == "not run" and r.suite not in allow:
            return 1
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        reports_dir = Path(tmp)

        # 1. JUnit (5 tests, 1 failure)
        d_junit = reports_dir / "suite_junit"
        d_junit.mkdir()
        (d_junit / "junit.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<testsuite name="junit" tests="5" failures="1" errors="0" skipped="0">\n'
            '  <testcase name="t1"/>\n'
            '  <testcase name="t2"/>\n'
            '  <testcase name="t3"/>\n'
            '  <testcase name="t4"/>\n'
            '  <testcase name="t5"><failure message="boom">fail</failure></testcase>\n'
            '</testsuite>\n',
            encoding="utf-8",
        )

        # 2. TestNG (10 passed)
        d_testng = reports_dir / "suite_testng"
        d_testng.mkdir()
        (d_testng / "testng-results.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<testng-results total="10" passed="10" failed="0" skipped="0">\n'
            '</testng-results>\n',
            encoding="utf-8",
        )

        # 3. Robot output.xml (8 pass, 2 fail, 1 skip)
        d_robot = reports_dir / "suite_robot"
        d_robot.mkdir()
        (d_robot / "output.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<robot>\n'
            '  <statistics>\n'
            '    <total>\n'
            '      <stat pass="8" fail="2" skip="1">All Tests</stat>\n'
            '    </total>\n'
            '  </statistics>\n'
            '</robot>\n',
            encoding="utf-8",
        )

        # 4. Two axe JSON files (one clean, one with a serious violation)
        d_a11y = reports_dir / "suite_a11y"
        (d_a11y / "a11y").mkdir(parents=True)
        (d_a11y / "a11y" / "view1.json").write_text(
            json.dumps({"view": "login", "violations": []}),
            encoding="utf-8",
        )
        (d_a11y / "a11y" / "view2.json").write_text(
            json.dumps({
                "view": "dashboard",
                "violations": [{
                    "id": "color-contrast",
                    "impact": "serious",
                    "help": "Elements must have sufficient color contrast",
                    "nodes": [{"target": ["#b1"]}, {"target": ["#b2"]}],
                }],
            }),
            encoding="utf-8",
        )

        # 5. Empty suite directory
        d_empty = reports_dir / "suite_empty"
        d_empty.mkdir()

        # 6. Corrupt XML file
        d_corrupt = reports_dir / "suite_corrupt"
        d_corrupt.mkdir()
        (d_corrupt / "junit.xml").write_text(
            "<testsuite tests='5'>not valid xml",
            encoding="utf-8",
        )

        results = collect(reports_dir)
        by_suite = {r.suite: r for r in results}

        # Assert counts match
        total_p, total_f, total_s = overall(results)
        assert total_p == 23, f"expected 23 passed, got {total_p}"
        assert total_f == 5, f"expected 5 failed, got {total_f}"
        assert total_s == 1, f"expected 1 skipped, got {total_s}"
        print("collected counts match: 23 passed, 5 failed, 1 skipped")

        # Corrupt file reported as failure and not as zero tests
        corrupt_res = by_suite["suite_corrupt"]
        assert corrupt_res.failed > 0 and corrupt_res.passed == 0, f"corrupt suite not failed: {corrupt_res}"
        assert "parse error" in corrupt_res.detail, f"corrupt detail missing 'parse error': {corrupt_res.detail}"
        print("corrupt file reported as failure and not as zero tests")

        # Empty directory reported as not run
        empty_res = by_suite["suite_empty"]
        assert empty_res.detail == "not run" and empty_res.passed == 0 and empty_res.failed == 0, f"empty directory unexpected: {empty_res}"
        print("empty directory reported as not run")

        # Rendered markdown holds one table row per suite and the two accessibility findings
        rendered = render(results, generated_for="test-commit")
        for s in ("suite_junit", "suite_testng", "suite_robot", "suite_a11y", "suite_empty", "suite_corrupt"):
            assert f"| {s} |" in rendered, f"missing table row for {s}"
        assert "login" in rendered and "No accessibility violations found." in rendered, "missing login accessibility finding"
        assert "dashboard" in rendered and "color-contrast" in rendered and "serious" in rendered, "missing dashboard accessibility finding"
        print("rendered markdown holds one table row per suite and the two accessibility findings")

        # Pass-rate arithmetic is right
        assert badge_for(by_suite["suite_junit"])[0] == "80.0%", "junit pass rate mismatch"
        assert badge_for(by_suite["suite_testng"])[0] == "100.0%", "testng pass rate mismatch"
        assert badge_for(by_suite["suite_robot"])[0] == "80.0%", "robot pass rate mismatch"
        assert badge_for(by_suite["suite_a11y"])[0] == "50.0%", "a11y pass rate mismatch"
        print("pass-rate arithmetic is right")

        # Exit code is 1
        assert exit_code(results) == 1, "expected overall exit code 1"
        print("overall exit code is 1 on failures and unexcused missing suites")

        # --allow-missing flips only the exit code and never the report body
        clean_and_empty = [
            SuiteResult(suite="suite_testng", passed=10, failed=0, skipped=0, detail="10 passed"),
            SuiteResult(suite="suite_empty", passed=0, failed=0, skipped=0, detail="not run"),
        ]
        body_before = render(clean_and_empty, generated_for="test")
        code_without_allow = exit_code(clean_and_empty)
        code_with_allow = exit_code(clean_and_empty, allow_missing={"suite_empty"})
        body_after = render(clean_and_empty, generated_for="test")

        assert code_without_allow == 1, "expected missing suite to exit 1"
        assert code_with_allow == 0, "expected allowed missing suite to exit 0"
        assert body_before == body_after, "--allow-missing modified rendered markdown"
        print("--allow-missing flips only the exit code and never the report body")

        # A reports tree that holds nothing must not render a green report: every suite this
        # repository owns is listed as not run and the gate exits 1.
        owned = expected_suites()
        assert len(owned) >= 5, f"expected the tests tree to yield the owned suites, got {owned}"
        for must in ("etsi", "security", "ogc"):
            assert must in owned, f"suite '{must}' missing from the owned suite list {owned}"

        nothing_ran = collect(reports_dir / "does-not-exist", expected=owned)
        assert [r.suite for r in nothing_ran] == owned, f"empty tree did not list every owned suite: {nothing_ran}"
        assert exit_code(nothing_ran) == 1, "a run where no suite reported anything exited 0"
        empty_body = render(nothing_ran, generated_for="test")
        for name in owned:
            assert f"| {name} | 0 | 0 | 0 | n/a | not run |" in empty_body, f"suite {name} not marked not run"
        assert exit_code(nothing_ran, allow_missing=set(owned)) == 0, "excusing every suite did not exit 0"
        print("an empty reports tree lists every owned suite as not run and exits 1")

        # Claims verification self-test: two tiny output.xml documents (one with temporal passing, one without)
        claims_file = reports_dir / "claims.json"
        claims_file.write_text(
            json.dumps({
                "ngsi-ld": ["core", "temporal"],
                "ogc": ["core", "oas30", "geojson", "crs", "basic-cql2"],
            }),
            encoding="utf-8",
        )

        pass_xml = reports_dir / "claims_pass.xml"
        pass_xml.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<robot>\n'
            '  <suite name="root">\n'
            '    <test name="T1"><tag>smoke</tag><status status="PASS"/></test>\n'
            '    <test name="T2"><tag>temporal</tag><status status="PASS"/></test>\n'
            '  </suite>\n'
            '</robot>\n',
            encoding="utf-8",
        )

        fail_xml = reports_dir / "claims_fail.xml"
        fail_xml.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<robot>\n'
            '  <suite name="root">\n'
            '    <test name="T1"><tag>smoke</tag><status status="PASS"/></test>\n'
            '    <test name="T2"><tag>temporal</tag><status status="FAIL"/></test>\n'
            '  </suite>\n'
            '</robot>\n',
            encoding="utf-8",
        )

        pass_problems = check_claims(claims_file, pass_xml)
        assert pass_problems == [], f"expected pass, got {pass_problems}"

        fail_problems = check_claims(claims_file, fail_xml)
        assert len(fail_problems) > 0, "expected problems for failed temporal tag"
        assert any("temporal" in p for p in fail_problems), f"expected 'temporal' in {fail_problems}"

        unclaimed_file = reports_dir / "claims_unclaimed.json"
        unclaimed_file.write_text(
            json.dumps({"ngsi-ld": ["core"], "ogc": []}),
            encoding="utf-8",
        )
        unclaimed_problems = check_claims(unclaimed_file, pass_xml)
        assert len(unclaimed_problems) > 0, "expected problem when temporal is unclaimed"
        assert any("temporal" in p for p in unclaimed_problems), f"expected 'temporal' in {unclaimed_problems}"
        print("claims check proves passing tags, missing claimed classes, and unclaimed suite tags")

    print("ok: qualification report aggregator proves counts, corrupt files, empty runs, a11y findings and exit codes")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reports", type=Path, help="root directory of suite reports")
    parser.add_argument("--output", type=Path, help="output markdown file (default: stdout)")
    parser.add_argument("--for", dest="generated_for", default="HEAD", help="commit/ref this report was generated for")
    parser.add_argument("--allow-missing", default="", help="comma-separated list of suites allowed to be not run")
    parser.add_argument("--claims", type=Path, help="path to claims.json to verify against robot output.xml")
    parser.add_argument("--robot-output", type=Path, help="path to specific robot output.xml for claims verification")
    parser.add_argument("--selftest", action="store_true", help="run internal test suite")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.claims:
        target_xml = args.robot_output
        if not target_xml and args.reports:
            if (args.reports / "output.xml").is_file():
                target_xml = args.reports / "output.xml"
            else:
                matches = sorted(args.reports.glob("**/output.xml"))
                if matches:
                    target_xml = matches[0]
        if not target_xml or not target_xml.is_file():
            print(f"claims check error: output.xml not found under {args.reports or args.robot_output}", file=sys.stderr)
            return 1
        claims_problems = check_claims(args.claims, target_xml)
        if claims_problems:
            for p in claims_problems:
                print(f"claims check error: {p}", file=sys.stderr)
            return 1
        if not args.output:
            print(f"ok: claims in {args.claims} verified against {target_xml}")
            return 0

    if not args.reports:
        parser.error("--reports directory is required (or use --claims with --robot-output, or --selftest)")

    allow_missing = {s.strip() for s in args.allow_missing.split(",") if s.strip()}
    results = collect(args.reports, expected=expected_suites())
    markdown = render(results, generated_for=args.generated_for)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)

    code = exit_code(results, allow_missing)
    p, f, s = overall(results)
    if code != 0:
        print(f"\nqualification gate failed: {p} passed, {f} failed, {s} skipped across {len(results)} suites", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Verdict on a TEAM Engine run of the OGC API - Features ETS (T-0057, TS-07, EP-30, EP-40).

TEAM Engine answers a run with a TestNG result document. TS-07 says the endpoint MUST pass the
Part 1 Core Abstract Test Suite, so any failed test method fails the build; a conformance class
that produced no test at all fails it too, because a suite that silently tested nothing is the
same as no suite.

    check_ogc_results.py reports/ogc/testng-results.xml
    check_ogc_results.py --selftest
"""

from __future__ import annotations

import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# EP-30: the classes the platform claims, hence the ones that must have run
REQUIRED_GROUPS = ("Core",)


def outcomes(results_xml: str) -> dict[str, dict[str, str]]:
    """{test group: {method: status}} — the group is the <test name> TEAM Engine reports."""
    grouped: dict[str, dict[str, str]] = {}
    for test in ET.parse(results_xml).getroot().iter("test"):
        methods = grouped.setdefault(test.get("name", ""), {})
        for method in test.iter("test-method"):
            if method.get("is-config") == "true":
                continue
            methods[method.get("name", "")] = method.get("status", "")
    return grouped


def check(grouped: dict[str, dict[str, str]]) -> list[str]:
    problems: list[str] = []
    for group, methods in sorted(grouped.items()):
        for name, status in sorted(methods.items()):
            if status == "FAIL":
                problems.append(f"{group}.{name}: FAIL — TS-07 allows no failure in the ATS")
    total = sum(len(methods) for methods in grouped.values())
    if total == 0:
        problems.append("the ETS ran no test method at all")
    for group in REQUIRED_GROUPS:
        if not any(name.lower().startswith(group.lower()) for name in grouped):
            problems.append(f"no {group} conformance class in the report: the run did not cover it")
    return problems


FIXTURE = """<testng-results>
  <suite name="ogcapi-features-1.0">
    <test name="{group}">
      <class name="org.opengis.cite.ogcapifeatures10.core.LandingPage">
        <test-method status="PASS" name="landingPageValidation"/>
        <test-method status="{status}" name="{failing}"/>
        <test-method status="PASS" name="setup" is-config="true"/>
      </class>
    </test>
  </suite>
</testng-results>
"""


def selftest() -> int:
    cases = [
        ("a clean Core run", {"group": "Core", "status": "PASS", "failing": "conformanceClasses"}, None),
        ("a failing Core assertion", {"group": "Core", "status": "FAIL", "failing": "geoJsonValidation"},
         "geoJsonValidation: FAIL"),
        ("a run without the Core class", {"group": "Coordinate Reference Systems", "status": "PASS",
                                          "failing": "crsHeader"}, "no Core conformance class"),
    ]
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "testng-results.xml"
        for name, fields, expected in cases:
            path.write_text(FIXTURE.format(**fields), encoding="utf-8")
            problems = check(outcomes(str(path)))
            if expected is None and problems:
                failures.append(f"{name}: reported {problems}")
            elif expected is not None and not any(expected in problem for problem in problems):
                failures.append(f"{name}: {expected!r} was not reported, got {problems}")

        path.write_text("<testng-results><suite name='x'/></testng-results>", encoding="utf-8")
        if not any("no test method at all" in problem for problem in check(outcomes(str(path)))):
            failures.append("an empty report was accepted as a pass")

        # a configuration method must not count as a test, or an empty run looks populated
        path.write_text(
            "<testng-results><suite name='x'><test name='Core'><class name='c'>"
            "<test-method status='PASS' name='setup' is-config='true'/></class></test></suite></testng-results>",
            encoding="utf-8",
        )
        if not any("no test method at all" in problem for problem in check(outcomes(str(path)))):
            failures.append("a report holding only configuration methods was accepted as a pass")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ok: a failed assertion, a missing conformance class and an empty report all go red")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--selftest"]:
        return selftest()
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    grouped = outcomes(argv[1])
    for group, methods in sorted(grouped.items()):
        tally = {status: sum(1 for s in methods.values() if s == status) for status in ("PASS", "FAIL", "SKIP")}
        # a skip is not a failure, but a conformance area nobody exercised is worth reading
        print(f"{group}: {tally['PASS']} passed, {tally['FAIL']} failed, {tally['SKIP']} skipped")
    problems = check(grouped)
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} problem(s) in the OGC ATS run — TS-07 allows none", file=sys.stderr)
        return 1
    print("ok: the endpoint passes the OGC API - Features Abstract Test Suite")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

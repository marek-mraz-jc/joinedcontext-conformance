#!/usr/bin/env python3
"""Compare a Robot Framework output.xml against the expected-failure quarantine list.

docs/Testing/02-conformance-tests.md §1: "Any unexpected failure fails the build immediately.
Any passing test that is present in the expected failure list also fails the build, forcing the
quarantine list to remain accurate." An entry past its expiration date fails the build too, so a
deviation cannot be parked forever (docs/Testing/00-strategy.md §5).

    check_expected_failures.py reports/etsi-ttf/output.xml tests/etsi/expected_failures.json
    check_expected_failures.py --selftest
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import xml.etree.ElementTree as ET


def robot_results(output_xml: str) -> dict[tuple[str, str], str]:
    """{(suite, test): status} for every test in the run, suite = innermost suite name."""
    results: dict[tuple[str, str], str] = {}
    for suite in ET.parse(output_xml).getroot().iter("suite"):
        for test in suite.findall("test"):
            status = test.find("status")
            results[(suite.get("name", ""), test.get("name", ""))] = (
                status.get("status", "") if status is not None else ""
            )
    return results


def check(results: dict[tuple[str, str], str], expected: list[dict], today: dt.date) -> list[str]:
    """Every reason the build must go red, most useful first. Empty list = green."""
    problems: list[str] = []
    quarantined = set()

    for entry in expected:
        try:
            key = (entry["suite"], entry["test_case"])
            expiration = dt.date.fromisoformat(entry["expiration"])
            reason = entry["reason"]
        except (KeyError, ValueError) as exc:
            problems.append(f"malformed quarantine entry {entry!r}: {exc}")
            continue
        quarantined.add(key)
        if not reason.strip():
            problems.append(f"quarantine entry {key[0]}.{key[1]} has an empty reason")
        if expiration < today:
            problems.append(
                f"quarantine of {key[0]}.{key[1]} expired on {expiration}: fix it or re-justify it"
            )
        status = results.get(key)
        if status is None:
            problems.append(
                f"quarantined test {key[0]}.{key[1]} did not run: stale entry or renamed test"
            )
        elif status == "PASS":
            problems.append(
                f"{key[0]}.{key[1]} passes but is still quarantined: remove the entry"
            )

    for (suite, test), status in sorted(results.items()):
        if status == "FAIL" and (suite, test) not in quarantined:
            problems.append(f"unexpected failure: {suite}.{test}")

    return problems


def selftest() -> int:
    import os
    import tempfile

    xml = """<?xml version="1.0"?>
    <robot><suite name="root">
      <suite name="001_01"><test name="Create Entity"><status status="PASS"/></test>
        <test name="Create Entity Invalid Payload"><status status="FAIL"/></test></suite>
      <suite name="058_01"><test name="MQTT Notification"><status status="FAIL"/></test></suite>
    </suite></robot>"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "output.xml")
        with open(path, "w", encoding="utf8") as fh:
            fh.write(xml)
        results = robot_results(path)

    assert results[("001_01", "Create Entity")] == "PASS", results
    assert results[("058_01", "MQTT Notification")] == "FAIL", results
    assert len(results) == 3, results

    today = dt.date(2026, 9, 6)
    far = "2026-12-31"

    quarantine_both = [
        {"suite": "001_01", "test_case": "Create Entity Invalid Payload", "reason": "upstream #142", "expiration": far},
        {"suite": "058_01", "test_case": "MQTT Notification", "reason": "no MQTT broker in CI", "expiration": far},
    ]
    assert check(results, quarantine_both, today) == [], check(results, quarantine_both, today)

    # an unquarantined failure is reported
    assert any("unexpected failure: 058_01.MQTT Notification" in p for p in check(results, quarantine_both[:1], today))

    # a quarantined test that passes is reported
    passing = quarantine_both + [{"suite": "001_01", "test_case": "Create Entity", "reason": "x", "expiration": far}]
    assert any("passes but is still quarantined" in p for p in check(results, passing, today))

    # an expired quarantine is reported even though the test still fails
    expired = [dict(quarantine_both[0], expiration="2026-01-01"), quarantine_both[1]]
    assert any("expired on 2026-01-01" in p for p in check(results, expired, today))

    # a renamed or removed test is reported instead of silently passing
    stale = quarantine_both + [{"suite": "001_01", "test_case": "Gone", "reason": "x", "expiration": far}]
    assert any("did not run" in p for p in check(results, stale, today))

    # an entry without a reason is reported
    empty = [dict(quarantine_both[0], reason="  "), quarantine_both[1]]
    assert any("empty reason" in p for p in check(results, empty, today))

    # a malformed entry is reported, not crashed on
    broken = [{"suite": "001_01"}, quarantine_both[0], quarantine_both[1]]
    assert any("malformed quarantine entry" in p for p in check(results, broken, today))

    print("check_expected_failures selftest ok")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[2], encoding="utf8") as fh:
        expected = json.load(fh)
    problems = check(robot_results(argv[1]), expected, dt.date.today())
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} conformance problem(s)", file=sys.stderr)
        return 1
    print(f"{len(expected)} quarantined, no unexpected failure")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

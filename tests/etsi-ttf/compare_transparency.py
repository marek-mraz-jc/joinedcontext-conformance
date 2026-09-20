#!/usr/bin/env python3
"""Gateway transparency: the ETSI suite must not notice the gateway (T-0056, TS-06, R15).

Compares two Robot Framework runs of the same suite — one straight at the broker, one through
the Context Gateway under an unrestricted administrative policy. R15 says the gateway introduces
zero specification divergence, so the only verdict that matters is per test case:

    passes at the broker, fails through the gateway  -> divergence, the build goes red
    ran in one leg but not in the other              -> the two legs are not comparable, red
    fails in both                                    -> the broker's own conformance gap (T-0054)
    fails at the broker, passes through the gateway  -> reported: the gateway is hiding something

    compare_transparency.py direct/output.xml gateway/output.xml
    compare_transparency.py --selftest
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from check_expected_failures import robot_results


def compare(direct: dict[tuple[str, str], str], gateway: dict[tuple[str, str], str]) -> tuple[list[str], list[str]]:
    """(problems that fail the build, findings worth reading)."""
    problems: list[str] = []
    findings: list[str] = []

    for key in sorted(set(direct) - set(gateway)):
        problems.append(f"{key[0]}.{key[1]} ran at the broker but not through the gateway")
    for key in sorted(set(gateway) - set(direct)):
        problems.append(f"{key[0]}.{key[1]} ran through the gateway but not at the broker")

    for key in sorted(set(direct) & set(gateway)):
        broker_status, gateway_status = direct[key], gateway[key]
        if broker_status == "PASS" and gateway_status != "PASS":
            problems.append(
                f"{key[0]}.{key[1]}: PASS at the broker, {gateway_status or 'no status'} through "
                "the gateway — R15 divergence"
            )
        elif broker_status != "PASS" and gateway_status == "PASS":
            findings.append(
                f"{key[0]}.{key[1]}: {broker_status or 'no status'} at the broker but PASS through "
                "the gateway — the gateway is answering something the broker does not"
            )

    if not direct:
        problems.append("the broker leg contains no test at all")
    return problems, findings


def _fixture(directory: Path, name: str, tests: dict[str, str]) -> str:
    """A minimal Robot output.xml with the given test statuses."""
    body = "".join(
        f'<test name="{test}"><status status="{status}"/></test>' for test, status in tests.items()
    )
    path = directory / name
    path.write_text(f'<robot><suite name="Entities">{body}</suite></robot>', encoding="utf-8")
    return str(path)


def selftest() -> int:
    cases: list[tuple[str, dict[str, str], dict[str, str], str | None]] = [
        ("identical passes", {"a": "PASS", "b": "PASS"}, {"a": "PASS", "b": "PASS"}, None),
        ("the same failure in both legs", {"a": "FAIL"}, {"a": "FAIL"}, None),
        ("a test the gateway breaks", {"a": "PASS"}, {"a": "FAIL"}, "R15 divergence"),
        ("a test only the broker leg ran", {"a": "PASS", "b": "PASS"}, {"a": "PASS"},
         "ran at the broker but not through the gateway"),
        ("a test only the gateway leg ran", {"a": "PASS"}, {"a": "PASS", "b": "PASS"},
         "ran through the gateway but not at the broker"),
        ("an empty broker leg", {}, {}, "no test at all"),
    ]
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name, direct_tests, gateway_tests, expected in cases:
            direct = robot_results(_fixture(directory, "direct.xml", direct_tests))
            gateway = robot_results(_fixture(directory, "gateway.xml", gateway_tests))
            problems, _ = compare(direct, gateway)
            if expected is None and problems:
                failures.append(f"{name}: reported {problems}")
            elif expected is not None and not any(expected in problem for problem in problems):
                failures.append(f"{name}: {expected!r} was not reported, got {problems}")

        # a gateway that answers where the broker fails is a finding, not a build failure
        direct = robot_results(_fixture(directory, "direct.xml", {"a": "FAIL"}))
        gateway = robot_results(_fixture(directory, "gateway.xml", {"a": "PASS"}))
        problems, findings = compare(direct, gateway)
        if problems:
            failures.append(f"a gateway passing where the broker fails must not fail the build: {problems}")
        if not findings:
            failures.append("a gateway passing where the broker fails must still be reported")

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ok: divergence and non-comparable legs go red, an identical pair stays green")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--selftest"]:
        return selftest()
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    problems, findings = compare(robot_results(argv[1]), robot_results(argv[2]))
    for finding in findings:
        print(f"note: {finding}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} divergence(s) between the broker and the gateway — R15 allows none", file=sys.stderr)
        return 1
    print("ok: the gateway leg has the same outcome as the broker leg for every test case")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Verdict over an Eclipse Dataspace Protocol TCK run (T-0062, DS-05).

The TCK runtime exits 0 even when tests fail — a real run against a dead connector answers
"Failed tests: 65" and still leaves `$?` at 0 — so the log is the verdict, not the exit code.
This reads the log the container wrote and fails the suite on any failed test, on a run that
never reached "Test run complete", on a run that passed nothing, and on a run that never
exercised catalog, contract negotiation and transfer, which DS-05 requires all three of.

    check_dsp_results.py reports/dsp/tck.log
    check_dsp_results.py --selftest
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
STAMP = re.compile(r"^\[[0-9T:.\-]+\]\s*")
OUTCOME = re.compile(r"^(SUCCESSFUL|FAILED):\s*(\S+)")
COUNT = re.compile(r"^(Passed|Failed) tests:\s*(\d+)")
DETAIL = re.compile(r"^\[([A-Z_]+:[\d-]+):")
COMPLETE = "Test run complete"
# DS-05: the connector passes the protocol in all three areas, so all three must have run
FAMILIES = {"CAT": "catalog", "CN": "contract negotiation", "TP": "transfer process"}


def clean(text: str) -> list[str]:
    return [STAMP.sub("", ANSI.sub("", line)).strip() for line in text.splitlines()]


def check(log: str) -> list[str]:
    lines = clean(log)
    problems: list[str] = []
    passed: list[str] = []
    failed: list[str] = []
    counts: dict[str, int] = {}
    reasons: dict[str, str] = {}

    for index, line in enumerate(lines):
        outcome = OUTCOME.match(line)
        if outcome:
            (passed if outcome.group(1) == "SUCCESSFUL" else failed).append(outcome.group(2))
            continue
        count = COUNT.match(line)
        if count:
            counts[count.group(1)] = int(count.group(2))
            continue
        # the Failures block names the test as `[TP:03-01: title]` and the reason on the next line
        detail = DETAIL.match(line)
        if detail:
            reasons[detail.group(1)] = next(
                (lines[ahead] for ahead in range(index + 1, min(index + 4, len(lines))) if lines[ahead]), ""
            )

    if COMPLETE not in lines:
        problems.append("the TCK log never reached 'Test run complete' — the run was cut short")
    if not passed and not failed:
        problems.append("the TCK log holds no test outcome at all: the run never started or the log is not a TCK log")

    for identifier in failed:
        reason = reasons.get(identifier, "")
        problems.append(f"{identifier} failed" + (f" — {reason}" if reason else ""))

    if counts.get("Failed", 0) != len(failed):
        problems.append(
            f"the TCK counted {counts.get('Failed')} failures but the log names {len(failed)}: the log is truncated"
        )
    if counts.get("Passed", len(passed)) == 0:
        problems.append("the TCK passed no test at all — the connector answered nothing the TCK could verify")

    exercised = {identifier.split(":")[0].split("_")[0] for identifier in passed + failed}
    for prefix, area in FAMILIES.items():
        if prefix not in exercised:
            problems.append(f"no {area} test ran (no {prefix}:* outcome in the log), DS-05 needs all three")
    return problems


# both fixtures are excerpts of real runs of eclipsedataspacetck/dsp-tck-runtime:1.0.2
GREEN = """[2026-09-06T12:10:00.1] Running DSP TCK v2025.1
[2026-09-06T12:10:01.1] Started: CN:01-01: Verify contract request
[2026-09-06T12:10:01.9] SUCCESSFUL: CN:01-01
[2026-09-06T12:10:02.1] Started: TP:01-01: Verify transfer request
[2026-09-06T12:10:02.9] SUCCESSFUL: TP:01-01
[2026-09-06T12:10:37.4] Started: CAT:01-01: Verify catalog request
[2026-09-06T12:10:37.9] SUCCESSFUL: CAT:01-01
[2026-09-06T12:10:38.2] Passed tests: 3
[2026-09-06T12:10:38.2] Failed tests: 0
[2026-09-06T12:10:38.2] Test run complete
"""

RED = """[2026-09-06T12:11:01.0] Running DSP TCK v2025.1
[2026-09-06T12:11:17.0] Started: TP:03-01: Verify transfer request, consumer completed
[2026-09-06T12:11:20.2] FAILED: TP:03-01
[2026-09-06T12:11:21.0] Started: CN:01-01: Verify contract request
[2026-09-06T12:11:22.2] SUCCESSFUL: CN:01-01
[2026-09-06T12:11:25.6] Started: CAT:01-03: Verify dataset request not found
[2026-09-06T12:11:25.6] SUCCESSFUL: CAT:01-03
[2026-09-06T12:11:25.6] Passed tests: 2
[2026-09-06T12:11:25.6] Failed tests: 1
\x1b[31m
[2026-09-06T12:11:25.6] Failures:
[2026-09-06T12:11:25.6]\x20
   ■ org.eclipse.dataspacetck.dsp.verification.tp.TransferProcessProvider03Test.tp_03_01

[2026-09-06T12:11:25.6]      [TP:03-01: Verify transfer request, consumer completed]
[2026-09-06T12:11:25.6]      java.net.ConnectException: Failed to connect to /127.0.0.1:9
\x1b[0m
[2026-09-06T12:11:25.7] Test run complete
"""


def selftest() -> int:
    cases = [
        ("a run that passed everything", GREEN, None),
        ("a failed assertion", RED, "TP:03-01 failed"),
        ("a run the container killed", GREEN.replace("Test run complete", ""), "never reached 'Test run complete'"),
        ("a log that is not a TCK log", "Segmentation fault\n", "holds no test outcome at all"),
        ("a run that only reached the catalog", GREEN.replace("TP:01-01", "CAT:01-02").replace("CN:01-01", "CAT:01-03"),
         "no contract negotiation test ran"),
        ("a truncated failure list", RED.replace("Failed tests: 1", "Failed tests: 4"), "the log is truncated"),
        ("a connector that answered nothing", GREEN.replace("Passed tests: 3", "Passed tests: 0"), "passed no test at all"),
    ]
    failures = []
    for name, log, expected in cases:
        problems = check(log)
        if expected is None and problems:
            failures.append(f"{name}: reported {problems}")
        elif expected is not None and not any(expected in problem for problem in problems):
            failures.append(f"{name}: {expected!r} was not reported, got {problems}")
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1
    print("ok: a failed assertion, a cut-short run, a missing protocol area and an empty run all go red")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--selftest"]:
        return selftest()
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"{path} does not exist — the TCK wrote no log", file=sys.stderr)
        return 1
    problems = check(path.read_text(encoding="utf-8", errors="replace"))
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} DSP conformance problem(s) in {path} — DS-05 requires none", file=sys.stderr)
        return 1
    print(f"dsp conformance ok ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

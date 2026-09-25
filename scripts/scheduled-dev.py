#!/usr/bin/env python3
"""Run the conformance suites of one schedule against dev and write a summary (T-2798, EP-01, OPS-35).

    scripts/scheduled-dev.py hourly|6h|nightly --out <summary.json> [--suites etsi,mcp]

The sandbox's batch runs it (hourly: ETSI smoke; every 6 hours: OGC, STA, MCP, CKAN; nightly:
schemathesis, DSP) and hands the summary to `tasks/file-failures`, which files one task per
new failure. Each suite runs with its `tests/dev.env.sh` profile, which mints the suite's own
least-role token from the cluster at run time, and writes JUnit XML into
`reports/scheduled/<suite>/`; every test case becomes one result keyed `<suite>/<case>`.

- ETSI is `scripts/etsi-smoke-dev.sh`, the 31 smoke cases. The Testing Task Force suite never
  runs against dev (the owner's rule); nothing here can name it.
- schemathesis runs GET and HEAD only: no throwaway project with an account bound to it alone
  exists on dev yet, and a fuzzer never writes into the demo projects.
- A suite that cannot start (no target on dev, a tool missing, a token not minted) or that
  exits non-zero without a failing case is one `error` result, never a pass.

Exit 0 when nothing failed, 1 otherwise; the summary is written either way.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEDULES = {
    "hourly": ("etsi",),
    "6h": ("ogc", "sta", "mcp", "ckan"),
    "nightly": ("schemathesis", "dsp"),
}
# What runs for a suite: its runner, the arguments it gets, and how long it may take. Every
# runner but ETSI's gets its `tests/dev.env.sh` profile; etsi-smoke-dev.sh sources its own.
RUNNERS = {
    "etsi": ("scripts/etsi-smoke-dev.sh", (), 900),
    "ogc": ("tests/ogc/run.sh", (), 1800),
    "sta": ("tests/sta/run.sh", (), 900),
    "mcp": ("tests/mcp/run.sh", (), 900),
    "ckan": ("tests/ckan/run.sh", (), 900),
    "schemathesis": ("tests/schemathesis/run.sh", ("--include-method", "GET", "--include-method", "HEAD"), 5400),
    "dsp": ("tests/dsp/run.sh", (), 3600),
}
REQUIREMENTS = ["EP-01", "OPS-35"]
TAIL = 400
QUARANTINE = ROOT / "tests/etsi/expected_failures_gateway.json"


def result(key: str, verdict: str, title: str, detail: str = "", evidence: str = "") -> dict:
    return {"key": key, "verdict": verdict, "title": title, "detail": detail, "evidence": evidence}


def tail(text: str) -> str:
    text = " ".join(text.split())
    return text[-TAIL:]


def junit_cases(path: Path) -> list[tuple[str, str, str, str]]:
    """(classname, name, verdict, message) of every test case in a JUnit file; [] if it is not one."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    if root.tag not in ("testsuites", "testsuite"):
        return []
    cases = []
    for case in root.iter("testcase"):
        verdict, message = "pass", ""
        for tag, outcome in (("failure", "fail"), ("error", "error"), ("skipped", "skip")):
            found = case.find(tag)
            if found is not None:
                verdict = outcome
                message = found.get("message") or (found.text or "")
                break
        cases.append((case.get("classname", ""), case.get("name", ""), verdict, message))
    return cases


def quarantine(path: Path) -> set[tuple[str, str]]:
    """(suite, test_case) of an ETSI expected-failure list; the cases it holds are known failures."""
    return {(entry["suite"], entry["test_case"]) for entry in json.loads(path.read_text())}


def suite_results(suite: str, reports: Path, code: int, output: str,
                  known: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """The results of one suite run: one per test case, or one `error` when nothing was measured."""
    results = []
    for path in sorted(reports.rglob("*.xml")):
        for classname, name, verdict, message in junit_cases(path):
            label = f"{classname}::{name}" if classname else name
            innermost = classname.rsplit(".", 1)[-1]
            if verdict == "fail" and (innermost, name) in known:
                verdict, message = "skip", "quarantined in the expected-failure list"
            results.append(result(
                f"{suite}/{label}", verdict,
                f"{suite} on dev: {name} " + {"pass": "passes", "skip": "skipped", "fail": "fails",
                                              "error": "errors"}[verdict],
                tail(message) if verdict != "pass" else "",
                str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            ))
    failing = any(r["verdict"] in ("fail", "error") for r in results)
    if not results:
        results.append(result(
            f"{suite}/run", "error", f"{suite} on dev did not start: no test case was reported",
            f"exit {code}: {tail(output)}",
        ))
    elif all(r["verdict"] == "skip" for r in results):
        results.append(result(
            f"{suite}/run", "error", f"{suite} on dev measured nothing: every case skipped",
            f"exit {code}: {tail(output)}",
        ))
    elif code != 0 and not failing:
        results.append(result(
            f"{suite}/run", "error", f"{suite} on dev exited {code} with no failing case",
            tail(output),
        ))
    return results


def run_suite(suite: str, reports: Path) -> list[dict]:
    runner, args, timeout = RUNNERS[suite]
    reports.mkdir(parents=True, exist_ok=True)
    for stale in reports.rglob("*.xml"):
        stale.unlink()
    if suite == "etsi":
        command = [runner, *args]
    else:
        command = ["bash", "-c", '. tests/dev.env.sh "$1" >&2; shift; exec "$@"', "_", suite, runner, *args]
    env = dict(os.environ, JC_REPORTS_DIR=str(reports))
    # Its own process group, so a timeout also stops what the runner started (robot, docker run).
    proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        output, code = f"{output}\ntimed out after {timeout} s", 124
    (reports / "run.log").write_text(output)
    return suite_results(suite, reports, code, output, quarantine(QUARANTINE) if suite == "etsi" else set())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("schedule", choices=sorted(SCHEDULES))
    parser.add_argument("--out", required=True, type=Path, help="where the summary JSON goes")
    parser.add_argument("--suites", default="", help="comma-separated subset of the schedule's suites")
    args = parser.parse_args(argv)
    suites = SCHEDULES[args.schedule]
    if args.suites:
        chosen = [s for s in args.suites.split(",") if s]
        if unknown := [s for s in chosen if s not in suites]:
            parser.error(f"not in the {args.schedule} schedule: {', '.join(unknown)} (it runs {', '.join(suites)})")
        suites = tuple(chosen)
    if not os.environ.get("KUBECONFIG"):
        parser.error("KUBECONFIG must point at the dev cluster: the suites' tokens are minted from it")
    results = []
    for suite in suites:
        print(f"=== {suite}", flush=True)
        results += run_suite(suite, ROOT / "reports" / "scheduled" / suite)
    summary = {
        "check": "conformance",
        "repo": "joinedcontext-conformance",
        "run": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ") + f" {args.schedule}",
        "requirements": REQUIREMENTS,
        "results": results,
    }
    args.out.write_text(json.dumps(summary, indent=2) + "\n")
    bad = [r for r in results if r["verdict"] in ("fail", "error")]
    for r in bad:
        print(f"{r['verdict']:5} {r['key']}")
    print(f"{len(results)} results, {len(bad)} failing; summary in {args.out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Source vs space of every snapshot pipeline on dev, nightly (T-4892, PL-97, PL-100).

    check.py [--junit out.xml] [--max-age-hours 48] [--max-missing 10]

Reads PORTAL_URL, PORTAL_TOKEN (a person who reads the projects' pipelines) and
SOURCE_REPORT_PROJECTS (space-separated). For each pipeline whose `spec.sourceDeletes.mode` is
`snapshot` it reads `GET /api/v1/projects/{project}/pipelines/{name}/source-deletes` and writes one
JUnit case, red when:

- the report cannot be read;
- a run is held (share exceeded, empty or incomplete): nothing is deleted until a steward decides;
- no complete run in `--max-age-hours`: the space can no longer be proved against its source;
- more than `--max-missing` records of the last complete run are missing from the space.

Ids only: the cases name counts, runs and reasons, never a record. No snapshot pipeline in any
project is one red case, since the check would prove nothing. Exit 0 when every case passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from xml.sax.saxutils import escape, quoteattr

TIMEOUT = 30


def judge(
    report: dict, now: datetime, max_age: timedelta, max_missing: int
) -> list[str]:
    """What is wrong with one pipeline's report, one sentence each; empty when it is sound."""
    found = []
    hold = report.get("hold")
    if hold:
        found.append(
            f"run {hold.get('run')} is held ({hold.get('reason')}): it would delete {hold.get('would')} "
            f"of {hold.get('ledger')} entities and deleted nothing"
        )
    last = report.get("lastComplete")
    if not last:
        found.append("no run has read the source completely")
    else:
        at = datetime.fromisoformat(str(last.get("at", "")))
        if now - at > max_age:
            found.append(
                f"the last complete run {last.get('run')} ended {at.isoformat()}, older than {max_age}"
            )
    missing = int(report.get("missing", 0))
    if missing > max_missing:
        found.append(
            f"{missing} records of the last complete run are missing from the space (refused)"
        )
    return found


def get(base: str, token: str, path: str) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
        return json.load(answer)


def junit(cases: list[tuple[str, list[str]]]) -> str:
    failures = sum(1 for _, found in cases if found)
    lines = [
        f'<testsuite name="source-report" tests="{len(cases)}" failures="{failures}">'
    ]
    for name, found in cases:
        if found:
            message = "; ".join(found)
            lines.append(
                f"  <testcase name={quoteattr(name)}><failure message={quoteattr(message)}>{escape(message)}</failure></testcase>"
            )
        else:
            lines.append(f"  <testcase name={quoteattr(name)}/>")
    lines.append("</testsuite>")
    return "\n".join(lines) + "\n"


def run(
    base: str, token: str, projects: list[str], max_age: timedelta, max_missing: int
) -> list[tuple[str, list[str]]]:
    now = datetime.now(UTC)
    cases: list[tuple[str, list[str]]] = []
    seen = 0
    for project in projects:
        quoted = urllib.parse.quote(project, safe="")
        try:
            pipelines = get(base, token, f"/api/v1/projects/{quoted}/pipelines").get(
                "items", []
            )
        except (urllib.error.URLError, ValueError) as error:
            cases.append(
                (
                    f"{project}/pipelines",
                    [f"the pipelines could not be listed: {error}"],
                )
            )
            continue
        for pipeline in pipelines:
            spec = pipeline.get("spec") or {}
            if (spec.get("sourceDeletes") or {}).get("mode") != "snapshot":
                continue
            seen += 1
            name = pipeline["metadata"]["name"]
            path = f"/api/v1/projects/{quoted}/pipelines/{urllib.parse.quote(name, safe='')}/source-deletes"
            try:
                found = judge(get(base, token, path), now, max_age, max_missing)
            except (urllib.error.URLError, ValueError, KeyError) as error:
                found = [f"the report could not be read: {error}"]
            cases.append((f"{project}/{name}", found))
    if seen == 0:
        cases.append(
            (
                "snapshot-pipelines",
                [f"no snapshot pipeline in {', '.join(projects)}: nothing is proved"],
            )
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", default="")
    parser.add_argument("--max-age-hours", type=int, default=48)
    parser.add_argument("--max-missing", type=int, default=10)
    args = parser.parse_args()
    base, token = os.environ.get("PORTAL_URL", ""), os.environ.get("PORTAL_TOKEN", "")
    projects = os.environ.get("SOURCE_REPORT_PROJECTS", "helsinki").split()
    if not base or not token:
        print(
            "PORTAL_URL and PORTAL_TOKEN are needed (. tests/dev.env.sh source-report)",
            file=sys.stderr,
        )
        return 2
    cases = run(
        base, token, projects, timedelta(hours=args.max_age_hours), args.max_missing
    )
    for name, found in cases:
        print(
            f"{'FAIL' if found else 'ok  '} {name}{': ' + '; '.join(found) if found else ''}"
        )
    if args.junit:
        with open(args.junit, "w", encoding="utf-8") as out:
            out.write(junit(cases))
    return 1 if any(found for _, found in cases) else 0


if __name__ == "__main__":
    sys.exit(main())

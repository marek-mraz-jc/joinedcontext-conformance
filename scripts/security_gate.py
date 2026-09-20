#!/usr/bin/env python3
"""The production security gate: one row per attack vector, a named test per row (T-1722, OPS-27, TS-19).

`docs/Testing/06-security-tests.md` carries the register of attack vectors: the surface it lives
on, the attack, the task that owns it, the requirements it proves, the test that replays it and
its state. This script is what makes that table a gate rather than a list.

    security_gate.py [--page <06-security-tests.md>] [--repo name=path ...] [--docs <path>]
                     [--open-is-red | --no-open-is-red]

It fails when a row is malformed, when it names a requirement `docs/Requirements` does not define,
when a `proven` row names a test that does not exist or is switched off, when a row that is not
`proven` names a test anyway, and, unless `--no-open-is-red` is passed, when any row is still
open. Every vector here is a priority 1 defence, so an open row blocks the first production apply:
the owner signs this table before go-live, and "Never forced" applies to the signature.

The test names are resolved through the same index `compliance.py` builds, so a test renamed in a
repository turns this gate red instead of quietly ceasing to prove anything.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SPECIFICATION = importlib.util.spec_from_file_location(
    "compliance", Path(__file__).resolve().parent / "compliance.py")
assert SPECIFICATION and SPECIFICATION.loader
compliance = importlib.util.module_from_spec(SPECIFICATION)
sys.modules["compliance"] = compliance
SPECIFICATION.loader.exec_module(compliance)

HEADING = "## 7. The production security gate"
COLUMNS = ["Surface", "Vector", "Task", "Requirements", "Test", "State"]
STATES = {"proven", "open"}
TASK_ID = re.compile(r"^T-\d{4}$")
CODE_SPAN = re.compile(r"`([^`]+)`")
TEST_REFERENCE = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+)/(?P<path>[^:]+)::(?P<name>.+)$")


@dataclass(frozen=True)
class Row:
    line: int
    surface: str
    vector: str
    task: str
    requirements: tuple[str, ...]
    tests: tuple[str, ...]
    state: str


def parse_table(page: str) -> tuple[list[Row], list[str]]:
    """The one table under `HEADING`. Malformed rows are reported, never silently dropped."""
    problems: list[str] = []
    lines = page.splitlines()
    try:
        start = next(number for number, line in enumerate(lines) if line.strip() == HEADING)
    except StopIteration:
        return [], [f"the page carries no `{HEADING}` section"]

    header = next((number for number in range(start, len(lines))
                   if lines[number].lstrip().startswith("| Surface")), None)
    if header is None:
        return [], [f"`{HEADING}` carries no table starting with | {' | '.join(COLUMNS)} |"]
    if [cell.strip() for cell in lines[header].strip().strip("|").split("|")] != COLUMNS:
        problems.append(f"line {header + 1}: the columns must be exactly {', '.join(COLUMNS)}")

    rows: list[Row] = []
    seen: dict[str, int] = {}
    for number in range(header + 2, len(lines)):
        line = lines[number].strip()
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(COLUMNS):
            problems.append(f"line {number + 1}: {len(cells)} cells, {len(COLUMNS)} expected: {line[:80]}")
            continue
        surface, vector, task, requirements, tests, state = cells
        if not TASK_ID.match(task):
            problems.append(f"line {number + 1}: the Task cell must be one T-xxxx id, not {task!r}")
        if state not in STATES:
            problems.append(f"line {number + 1}: the State cell must be one of {', '.join(sorted(STATES))}, not {state!r}")
        if vector in seen:
            problems.append(f"line {number + 1}: the vector {vector!r} is already a row at line {seen[vector]}")
        seen[vector] = number + 1
        rows.append(Row(
            line=number + 1,
            surface=surface,
            vector=vector,
            task=task,
            requirements=tuple(part.strip() for part in requirements.split(",") if part.strip()),
            tests=tuple(CODE_SPAN.findall(tests)),
            state=state,
        ))
    if not rows:
        problems.append(f"`{HEADING}` holds a table with no row: the register cannot be empty")
    return rows, problems


def check_rows(rows: list[Row], index: dict) -> tuple[list[str], list[Row]]:
    """Every rule the table must satisfy, and the rows still open."""
    failures: list[str] = []
    defined = {requirement["id"] for requirement in index["requirements"]}
    by_reference: dict[str, dict] = {
        f"{test['repo']}/{test['path']}::{test['name']}": test for test in index["tests"]
    }

    for row in rows:
        if not row.requirements:
            failures.append(f"line {row.line}: {row.vector!r} names no requirement it proves")
        for requirement in row.requirements:
            if requirement not in defined:
                failures.append(
                    f"line {row.line}: {row.vector!r} names {requirement}, which docs/Requirements "
                    f"does not define"
                )
        if row.state != "proven":
            if row.tests:
                failures.append(
                    f"line {row.line}: {row.vector!r} is {row.state} and names a test anyway; a row "
                    f"with a test that replays the attack is `proven`"
                )
            continue
        if not row.tests:
            failures.append(f"line {row.line}: {row.vector!r} is proven by no named test")
        for reference in row.tests:
            if not TEST_REFERENCE.match(reference):
                failures.append(
                    f"line {row.line}: {reference!r} is not a test reference "
                    f"(repository/path::name)"
                )
                continue
            test = by_reference.get(reference)
            if test is None:
                failures.append(
                    f"line {row.line}: {row.vector!r} names {reference}, which no test of the five "
                    f"repositories is: renamed, moved, or never written"
                )
            elif test["skip"] == "unconditional":
                failures.append(
                    f"line {row.line}: {row.vector!r} is proven by {reference}, which is switched "
                    f"off: {test['skip_note'] or '(no reason given)'}"
                )
    return failures, [row for row in rows if row.state != "proven"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", type=Path, help="the security-tests page holding the register")
    parser.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--docs", type=Path)
    parser.add_argument("--open-is-red", action=argparse.BooleanOptionalAction, default=True,
                        help="an open row fails the gate (the default: it blocks go-live)")
    arguments = parser.parse_args(argv)

    here = Path(__file__).resolve().parent.parent.parent
    repos = compliance.resolve_repos(arguments.repo, here)
    docs = arguments.docs or repos.get("docs") or (here / "docs")
    page = arguments.page or (docs / "Testing" / "06-security-tests.md")
    if not page.is_file():
        raise SystemExit(f"{page}: no security-tests page here. Name it with --page.")

    if not (docs / "Requirements").is_dir():
        raise SystemExit(f"{docs}/Requirements is not a directory: name it with --docs")
    # Built here rather than read from `compliance/index.json`: a stale file would let a test that
    # has been renamed away keep ticking its vector.
    index = compliance.build_index(docs, repos)

    rows, problems = parse_table(page.read_text(encoding="utf-8"))
    failures, still_open = check_rows(rows, index)
    for problem in problems + failures:
        print(problem)

    print(f"\n{len(rows)} attack vectors, {len(rows) - len(still_open)} proven by a named test, "
          f"{len(still_open)} open")
    for row in still_open:
        print(f"  open: {row.surface} / {row.vector} ({row.task})")

    if problems or failures:
        print(f"\n{len(problems) + len(failures)} problem(s) in the register — OPS-27 requires none",
              file=sys.stderr)
        return 1
    if still_open and arguments.open_is_red:
        print(f"\n{len(still_open)} attack vector(s) are not proven by a test. Every row here is a "
              f"priority 1 defence, so the platform is not ready for its first production apply "
              f"(OPS-27). The owner signs this table when every row is proven.", file=sys.stderr)
        return 1
    print("ok: every attack vector of the register is replayed by a named test that runs in a lane")
    return 0


if __name__ == "__main__":
    sys.exit(main())

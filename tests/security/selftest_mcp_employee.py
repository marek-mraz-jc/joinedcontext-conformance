#!/usr/bin/env python3
"""Proof that test_mcp_employee_workflow.py goes red when the marker or the filter is removed (T-5559).

1. conforming: every case passes, none skipped.
2. unmarked: test_a_planted_instruction_comes_back_as_marked_data fails.
3. unfiltered: test_tools_list_matches_the_policy_per_role and test_a_viewer_is_offered_no_write fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stub_mcp_employee_server import Stub

SUITE = Path(__file__).resolve().parent / "test_mcp_employee_workflow.py"


def run(mode: str) -> tuple[int, str]:
    stub = Stub(mode)
    try:
        env = {**os.environ, **stub.env()}
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(SUITE), "-q", "-rfs", "-p", "no:cacheprovider"],
            capture_output=True, text=True, env=env, check=False,
        )
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        stub.shutdown()


def main() -> int:
    code, out = run("conforming")
    if code != 0 or "skipped" in out or "7 passed" not in out:
        print(f"FAIL conforming:\n{out}", file=sys.stderr)
        return 1
    print("case 1 ok: the conforming stub passes all 7 cases")
    for mode, red in [
        ("unmarked", ["test_a_planted_instruction_comes_back_as_marked_data"]),
        ("unfiltered", ["test_tools_list_matches_the_policy_per_role", "test_a_viewer_is_offered_no_write"]),
    ]:
        code, out = run(mode)
        failed = [line for line in out.splitlines() if line.startswith("FAILED ")]
        missing = [name for name in red if not any(f"::{name}" in line for line in failed)]
        if code == 0 or missing:
            print(f"FAIL {mode}: expected red {missing or red}:\n{out}", file=sys.stderr)
            return 1
        print(f"case {mode} ok: {', '.join(red)} red")
    return 0


if __name__ == "__main__":
    sys.exit(main())

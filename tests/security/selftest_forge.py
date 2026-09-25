#!/usr/bin/env python3
"""Proof that the forge side-door suite goes red on every door it claims to watch (T-1703).

`test_forge_side_door.py` only ever asserts refusals, and a suite of refusals passes beautifully
against a forge that is not there at all. So each case is run twice here: once against a stub
that answers the way the hardened forge must, where the whole suite is green, and once against
a stub with exactly one door open, where that case — and only the cases that door belongs to —
is named in the failures.

Runs in the fast `ci` lane: it needs no forge, no cluster and no credential.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stub_forge_server import (
    ADMIN_TOKEN,
    ORG,
    OTHER_ORG,
    OTHER_REPO,
    PLATFORM_TOKEN,
    READER_TOKEN,
    REPO,
    serve,
)

SUITE = Path(__file__).resolve().parent / "test_forge_side_door.py"

# mode -> the test that must name the door it opens.
DOORS = {
    "fork_allowed": "test_cc41_a_reader_cannot_fork_the_configuration_repository",
    "repo_creation_allowed": "test_cc41_a_reader_cannot_create_a_repository_of_their_own",
    "branch_unprotected":
        "test_cc41_pf51_the_default_branch_is_protected_and_a_reader_may_neither_push_nor_merge",
    "pull_request_outside_the_portal":
        "test_cc41_a_reader_cannot_open_a_pull_request_outside_the_portal",
    "reader_may_push_a_branch":
        "test_cc41_a_reader_cannot_push_to_the_branch_a_pull_request_is_reviewed_from",
    "cross_organisation_readable":
        "test_cc41_a_reader_cannot_read_another_organisations_repository",
    "reader_may_add_a_webhook":
        "test_cc41_a_reader_cannot_read_or_add_a_webhook_on_the_configuration_repository",
    "platform_token_is_an_administrator":
        "test_pf51_a_leaked_platform_token_cannot_administer_the_forge",
    "protection_without_review_rules":
        "test_pf51_the_protection_rule_pins_the_pusher_and_drops_an_approval_a_new_commit_invalidates",
    "reader_may_create_an_organisation": "test_cc41_a_reader_cannot_create_an_organization",
}


def run_suite(url: str) -> tuple[int, str]:
    env = os.environ.copy()
    env.update({
        "FORGE_URL": url,
        "FORGE_READER_TOKEN": READER_TOKEN,
        "FORGE_PLATFORM_TOKEN": PLATFORM_TOKEN,
        "FORGE_ADMIN_TOKEN": ADMIN_TOKEN,
        "FORGE_ORG": ORG,
        "FORGE_REPO": REPO,
        "FORGE_BRANCH": "main",
        "FORGE_OTHER_ORG": OTHER_ORG,
        "FORGE_OTHER_REPO": OTHER_REPO,
    })
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def main() -> int:
    failures = 0

    hardened = serve(mode="hardened")
    try:
        code, out = run_suite(hardened.url)
        if code != 0:
            print(f"FAIL: the hardened forge does not pass the suite:\n{out}", file=sys.stderr)
            failures += 1
        elif "skipped" in out:
            print(f"FAIL: a case skipped itself against the hardened forge:\n{out}", file=sys.stderr)
            failures += 1
        else:
            print("ok: the hardened forge passes every case")
    finally:
        hardened.shutdown()

    for mode, expected in DOORS.items():
        server = serve(mode=mode)
        try:
            code, out = run_suite(server.url)
            if code == 0:
                print(f"FAIL: {mode} opened a door and the suite stayed green:\n{out}",
                      file=sys.stderr)
                failures += 1
            elif expected not in out:
                print(f"FAIL: {mode} was not caught by {expected}:\n{out}", file=sys.stderr)
                failures += 1
            else:
                print(f"ok: {mode} is caught by {expected}")
        finally:
            server.shutdown()

    if failures:
        print(f"{failures} self-check(s) failed", file=sys.stderr)
        return 1
    print(f"the forge side-door suite is red for all {len(DOORS)} doors and green for none")
    return 0


if __name__ == "__main__":
    sys.exit(main())

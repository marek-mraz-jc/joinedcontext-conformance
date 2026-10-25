#!/usr/bin/env python3
"""Proof that `test_tenant_isolation.py` goes red when isolation is broken (T-1708; PF-32, PF-59).

A suite nobody has seen fail is a suite nobody can trust. The stub platform beside this file is
correct in `isolating` mode and wrong in one named way in each of the others; this runs the suite
against each mode and asserts which case catches which defect.

1. `isolating` passes the whole suite.
2. `leaky_list` fails `test_a_token_of_one_project_lists_nothing_of_the_other_on_any_collection`.
3. `existence_disclosure` fails `test_a_resource_of_the_other_project_is_not_found_by_name_either`.
4. `write_lands` fails `test_a_write_into_the_other_project_changes_nothing_there`.
5. `artifact_served` fails
   `test_an_artifact_of_the_other_project_is_not_fetched_with_this_projects_token`.
6. `mcp_follows_argument` fails
   `test_an_mcp_tool_call_naming_the_other_project_reads_nothing_of_it`.

The same holds for `test_tenant_matrix.py` (T-5557): `isolating` passes every row, and each of
`leaky_temporal`, `leaky_ws` and `leaky_hub_mcp` turns its own row red; and for
`test_tenancy_injection.py` (T-5566): `trusts_identity_headers`, a platform that lets `x-jc-*`,
`jc-provenance-*` and `l5d-*` through to B's doors, turns
`test_platform_identity_headers_from_outside_change_nothing` red.

Run it as `python3 tests/security/selftest_tenancy.py`; it needs no deployed system.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stub_tenancy_server import (
    ARTIFACT_B,
    DATAMODEL_B,
    ENTITY_B,
    PROJECT_A,
    PROJECT_B,
    REPO_B,
    RUN_B,
    SERVER_B,
    SLUG_B,
    SPACE_B,
    TOKEN_A,
    TOKEN_B,
    TYPE_B,
    serve,
)

HERE = Path(__file__).resolve().parent


def environment(url: str) -> dict[str, str]:
    """Every door of the suite, pointed at the stub."""
    return {
        "PORTAL_URL": url,
        "PROJECT_A": PROJECT_A,
        "PROJECT_B": PROJECT_B,
        "TOKEN_PROJECT_A": TOKEN_A,
        "TOKEN_PROJECT_B": TOKEN_B,
        "OTHER_ENDPOINT_URL": f"{url}/api/endpoint/{SLUG_B}/ngsi-ld/v1",
        "MCP_URL": f"{url}/mcp",
        "OTHER_ARTIFACT_URL": f"{url}/artifacts/{ARTIFACT_B}",
        "FORGE_URL": f"{url}/forge",
        "OTHER_FORGE_REPO": REPO_B,
        "OTHER_RUN_ID": RUN_B,
        # The broker's own address is not part of this proof: it is a network control, and a
        # stub answering on loopback would say nothing about a NetworkPolicy.
        "BROKER_URL": "",
        # test_tenant_matrix.py: the gateway and the Portal are the one stub.
        "GATEWAY_URL": url,
        "OTHER_SLUG": SLUG_B,
        "OTHER_SPACE": SPACE_B,
        "OTHER_TYPE": TYPE_B,
        "OTHER_ENTITY": ENTITY_B,
        "OTHER_MCP_SERVER": SERVER_B,
        "OTHER_DATAMODEL": DATAMODEL_B,
    }

ISOLATION = "test_tenant_isolation.py"
MATRIX = "test_tenant_matrix.py"
INJECTION = "test_tenancy_injection.py"


def run(url: str, suite: str) -> tuple[int, str]:
    env = os.environ.copy()
    env.update(environment(url))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(HERE / suite), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def case(mode: str, must_fail: str | None, suite: str = ISOLATION) -> bool:
    stub = serve(mode=mode)
    try:
        code, out = run(stub.url, suite)
    finally:
        stub.close()

    if must_fail is None:
        if code != 0 or "passed" not in out:
            print(f"FAIL {mode}: the suite did not pass against a platform that isolates:\n{out}", file=sys.stderr)
            return False
        return True

    if code == 0:
        print(f"FAIL {mode}: the suite passed against a platform that does not isolate:\n{out}", file=sys.stderr)
        return False
    if must_fail not in out:
        print(f"FAIL {mode}: the suite went red, but not on {must_fail}:\n{out}", file=sys.stderr)
        return False
    return True


def main() -> int:
    cases = [
        ("isolating", None),
        ("leaky_list", "test_a_token_of_one_project_lists_nothing_of_the_other_on_any_collection"),
        ("existence_disclosure", "test_a_resource_of_the_other_project_is_not_found_by_name_either"),
        ("write_lands", "test_a_write_into_the_other_project_changes_nothing_there"),
        ("artifact_served", "test_an_artifact_of_the_other_project_is_not_fetched_with_this_projects_token"),
        ("mcp_follows_argument", "test_an_mcp_tool_call_naming_the_other_project_reads_nothing_of_it"),
    ]
    row = "test_surface_verb_of_the_other_project_answers_like_a_missing_one"
    matrix = [
        ("isolating", None),
        ("leaky_temporal", f"{MATRIX}::{row}[temporal-history]"),
        ("leaky_ws", f"{MATRIX}::{row}[websocket-stream]"),
        ("leaky_hub_mcp", f"{MATRIX}::{row}[mcp-hub-read]"),
    ]
    spoof = "test_platform_identity_headers_from_outside_change_nothing"
    injection = [
        ("isolating", None),
        ("trusts_identity_headers", f"{INJECTION}::{spoof}[ngsi-ld-read]"),
    ]
    runs = [(mode, expected, ISOLATION) for mode, expected in cases]
    runs += [(mode, expected, MATRIX) for mode, expected in matrix]
    runs += [(mode, expected, INJECTION) for mode, expected in injection]
    ok = True
    for mode, expected, suite in runs:
        if case(mode, expected, suite):
            print(f"ok   {mode} ({suite})")
        else:
            ok = False
    if ok:
        print(f"{len(runs)} cases: both suites are green on isolation and red on every planted defect")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

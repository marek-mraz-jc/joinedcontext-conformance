#!/usr/bin/env python3
"""Proof that the T-0060 and T-0061 MCP conformance assertions can go red.

Runs pytest against tests/mcp twice using an in-process stub MCP server:
1. Conforming mode: must exit 0 with 0 skipped tests.
2. Broken mode: must exit non-zero, failing all required protocol and isolation checks.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from stub_mcp_server import serve  # noqa: E402

REQUIRED_FAILURES = [
    "test_sp14_jsonrpc_response_framing",
    "test_ag07_tool_behavior_annotations",
    "test_ag05_sp14_a_space_argument_never_moves_the_caller",
    "test_sp20_a_cross_space_probe_is_byte_identical_to_a_miss",
    "test_ag06_the_configuration_mcp_writes_nothing_directly",
    "test_ag11_an_agent_neither_approves_itself_nor_edits_the_lanes",
    "test_ag13_a_narrowed_answer_says_so_and_leaks_nothing",
    "test_sp17_private_mcp_requires_rfc9728_auth",
    "test_ep71_the_hub_says_what_it_federates_before_a_tool_is_called",
    "test_ep71_a_hub_tool_result_names_the_sources_it_is_a_union_over",
    "test_pf48_the_hub_grants_no_more_than_its_own_policy_says",
]


def make_env(port: int) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "MCP_URL": f"http://127.0.0.1:{port}/cs/ovzdusie/mcp",
            "OTHER_SPACE_MCP_URL": f"http://127.0.0.1:{port}/cs/doprava/mcp",
            "PRIVATE_MCP_URL": f"http://127.0.0.1:{port}/cs/tajne/mcp",
            "CONFIG_MCP_URL": f"http://127.0.0.1:{port}/config/mcp",
            "ENDPOINT_MCP_URL": f"http://127.0.0.1:{port}/api/endpoint/aq-public/mcp",
            "MCP_AGENT_TOKEN": "agent-token",
            "CONFIG_MCP_SCRATCH": "test-scratch",
            "MCP_HIDDEN_ATTR": "secretReading",
            "MCP_TOKEN": "viewer-token",
            # The hub surface (T-1209): a space of registrations over two others.
            "HUB_MCP_URL": f"http://127.0.0.1:{port}/cs/hub/mcp",
            "HUB_MEMBERS": "indicators,transport",
            "HUB_TYPE": "AirQualityObserved",
        }
    )
    return env


def run_pytest(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        # The Portal MCP suite (test_portal_mcp.py) speaks to the operation registry of a real
        # Portal (PORTAL_MCP_URL); the stub is the data plane's MCP and would only skip it.
        [sys.executable, "-m", "pytest", "tests/mcp", "-q", "--ignore=tests/mcp/test_portal_mcp.py"],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def main() -> int:
    failures = []

    # 1. Conforming run
    server_good, _ = serve(port=0, mode="conforming", agent_token="agent-token")
    port_good = server_good.server_address[1]
    try:
        good = run_pytest(make_env(port_good))
        if good.returncode != 0:
            failures.append(f"the conforming stub failed the run:\n{good.stdout}\n{good.stderr}")
        if "skipped" in good.stdout.lower():
            failures.append(f"the conforming run had unexpected skipped tests:\n{good.stdout}")
        passed = re.search(r"(\d+) passed", good.stdout)
        # a green run of two tests would prove nothing: every assertion of the suite must have run
        if not passed or int(passed.group(1)) <= len(REQUIRED_FAILURES):
            failures.append(f"the conforming run exercised too few tests:\n{good.stdout}")
    finally:
        server_good.shutdown()
        server_good.server_close()

    # 2. Broken run
    server_bad, _ = serve(port=0, mode="broken", agent_token="agent-token")
    port_bad = server_bad.server_address[1]
    try:
        bad = run_pytest(make_env(port_bad))
        if bad.returncode == 0:
            failures.append(f"the broken stub passed the run when it should have failed:\n{bad.stdout}")
        for check in REQUIRED_FAILURES:
            if check not in bad.stdout:
                failures.append(f"{check} did not fire on the broken stub:\n{bad.stdout}")
    finally:
        server_bad.shutdown()
        server_bad.server_close()

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    if failures:
        return 1

    print("ok: MCP framing and isolation assertions pass a conforming server and fail a broken one")
    return 0


if __name__ == "__main__":
    sys.exit(main())

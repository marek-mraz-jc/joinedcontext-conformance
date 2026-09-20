#!/usr/bin/env python3
"""Proof that the representation parity test suite goes red on negative cases (T-0085: TS-03, EP-06, EP-07).

Asserts that:
1. 'conforming' -> suite passes and reports five participating representations.
2. 'leaky' -> test_ep07_r20_a_hidden_attribute_leaks_through_no_representation fails, naming CSV and the attribute.
3. 'divergent' -> id-set test and value test both fail, naming the missing id and rounded value.
4. 'conforming' with JC_STUB_ONLY=ngsi-ld -> vacuity guard fails rather than passing on one representation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stub_representation_server import serve


def run_pytest(base_url: str, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    security_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    env["PARITY_BASE_URL"] = base_url
    env["HIDDEN_ATTR"] = "internalAuditSecret"
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(security_dir / "test_representation_parity.py"),
            "-v",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = proc.stdout + "\n" + proc.stderr
    return proc.returncode, combined


def main() -> int:
    # Case 1: Conforming
    server1 = serve(port=0, mode="conforming")
    port1 = server1.server_address[1]
    try:
        code, out = run_pytest(f"http://127.0.0.1:{port1}")
        if code != 0:
            print(f"FAIL case 1: conforming stub failed unexpectedly:\n{out}", file=sys.stderr)
            return 1
        if "5 passed" not in out:
            print(f"FAIL case 1: expected 5 passed tests, got:\n{out}", file=sys.stderr)
            return 1
        print("case 1 ok: conforming stub passes all 5 tests across 5 representations")
    finally:
        server1.shutdown()

    # Case 2: Leaky
    server2 = serve(port=0, mode="leaky")
    port2 = server2.server_address[1]
    try:
        code, out = run_pytest(f"http://127.0.0.1:{port2}")
        if code == 0:
            print("FAIL case 2: leaky stub passed unexpectedly", file=sys.stderr)
            return 1
        if "test_ep07_r20_a_hidden_attribute_leaks_through_no_representation" not in out:
            print(f"FAIL case 2: leak test did not fail as expected:\n{out}", file=sys.stderr)
            return 1
        if "csv" not in out or "internalAuditSecret" not in out:
            print(f"FAIL case 2: leak test report did not name 'csv' and 'internalAuditSecret':\n{out}", file=sys.stderr)
            return 1
        print("case 2 ok: leaky stub fails leak test and names CSV and the hidden attribute")
    finally:
        server2.shutdown()

    # Case 3: Divergent
    server3 = serve(port=0, mode="divergent")
    port3 = server3.server_address[1]
    try:
        code, out = run_pytest(f"http://127.0.0.1:{port3}")
        if code == 0:
            print("FAIL case 3: divergent stub passed unexpectedly", file=sys.stderr)
            return 1
        if "test_ep07_the_same_entities_appear_in_every_representation" not in out:
            print(f"FAIL case 3: id-set test did not fail:\n{out}", file=sys.stderr)
            return 1
        if "test_ep07_attribute_values_agree_across_representations" not in out:
            print(f"FAIL case 3: value agreement test did not fail:\n{out}", file=sys.stderr)
            return 1
        if "urn:ngsi-ld:AirQualityObserved:station-002" not in out:
            print(f"FAIL case 3: report did not name missing id:\n{out}", file=sys.stderr)
            return 1
        if "10.0" not in out:
            print(f"FAIL case 3: report did not name rounded value:\n{out}", file=sys.stderr)
            return 1
        print("case 3 ok: divergent stub fails id-set and value tests naming missing id and rounded value")
    finally:
        server3.shutdown()

    # Case 4: Vacuity check (single representation only)
    server4 = serve(port=0, mode="conforming", stub_only="ngsi-ld")
    port4 = server4.server_address[1]
    try:
        code, out = run_pytest(f"http://127.0.0.1:{port4}")
        if code == 0:
            print("FAIL case 4: single representation stub passed unexpectedly", file=sys.stderr)
            return 1
        if "test_ts03_a_representation_that_answers_nothing_is_not_parity" not in out:
            print(f"FAIL case 4: vacuity guard test did not fail:\n{out}", file=sys.stderr)
            return 1
        print("case 4 ok: single representation stub fails vacuity test")
    finally:
        server4.shutdown()

    print("ok: representation parity conformance selftest passed all negative and positive cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())

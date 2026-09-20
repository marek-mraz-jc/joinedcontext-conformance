#!/usr/bin/env python3
"""Proof that the AuthZEN discovery and ODRL mapping suites go red on negative cases (T-0086, T-0087).

Asserts that:
1. conforming mode passes both test_authzen_access.py and test_odrl_mapping.py.
2. lossy_odrl fails test_r26_r52_the_odrl_policy_carries_the_same_grants_as_the_json_document
   naming the lost scopeQ and the lost operation.
3. disclosing fails test_ep59_r20_the_document_hides_what_the_caller_may_not_see.
4. contradictory fails test_ep56_a_grant_that_grants_nothing_is_not_a_grant.
5. check_leaks fails test_r51_access_check_answers_the_authzen_evaluation_shape.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stub_access_server import serve


def run_pytest(extra_env: dict[str, str]) -> tuple[int, str]:
    test_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    env.update(extra_env)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(test_dir / "test_authzen_access.py"),
            str(test_dir / "test_odrl_mapping.py"),
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined_output = proc.stdout + "\n" + proc.stderr
    return proc.returncode, combined_output


def main() -> int:
    # 1. Conforming mode passes both suites
    s1 = serve(port=0, mode="conforming")
    try:
        code, out = run_pytest({
            "ACCESS_URL": f"{s1.url}/access",
            "ACCESS_CHECK_URL": f"{s1.url}/access/check",
            "DATA_URL": f"{s1.url}/ngsi-ld/v1",
            "FORBIDDEN_TYPE": "ServiceAccount",
            "HIDDEN_ATTR": "stationApiKey",
        })
        if code != 0 or "passed" not in out:
            print(f"FAIL case 1: conforming mode failed unexpectedly:\n{out}", file=sys.stderr)
            return 1
        print("case 1 ok: conforming mode passes both suites")
    finally:
        s1.shutdown()

    # 2. lossy_odrl drops scopeQ and updateEntity
    s2 = serve(port=0, mode="lossy_odrl")
    try:
        code, out = run_pytest({
            "ACCESS_URL": f"{s2.url}/access",
            "ACCESS_CHECK_URL": f"{s2.url}/access/check",
            "DATA_URL": f"{s2.url}/ngsi-ld/v1",
            "FORBIDDEN_TYPE": "ServiceAccount",
            "HIDDEN_ATTR": "stationApiKey",
        })
        if code == 0:
            print("FAIL case 2: lossy_odrl mode passed unexpectedly", file=sys.stderr)
            return 1
        if "test_r26_r52_the_odrl_policy_carries_the_same_grants_as_the_json_document" not in out:
            print(f"FAIL case 2: round trip test not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "scopeQ" not in out:
            print(f"FAIL case 2: lost scopeQ was not reported in output:\n{out}", file=sys.stderr)
            return 1
        if "updateEntity" not in out:
            print(f"FAIL case 2: lost operation updateEntity was not reported in output:\n{out}", file=sys.stderr)
            return 1
        print("case 2 ok: lossy_odrl fails round-trip test naming lost scopeQ and updateEntity")
    finally:
        s2.shutdown()

    # 3. disclosing mode discloses forbidden type and hidden attribute
    s3 = serve(port=0, mode="disclosing")
    try:
        code, out = run_pytest({
            "ACCESS_URL": f"{s3.url}/access",
            "ACCESS_CHECK_URL": f"{s3.url}/access/check",
            "DATA_URL": f"{s3.url}/ngsi-ld/v1",
            "FORBIDDEN_TYPE": "ServiceAccount",
            "HIDDEN_ATTR": "stationApiKey",
        })
        if code == 0:
            print("FAIL case 3: disclosing mode passed unexpectedly", file=sys.stderr)
            return 1
        if "test_ep59_r20_the_document_hides_what_the_caller_may_not_see" not in out:
            print(f"FAIL case 3: EP-59/R20 hiding test not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 3 ok: disclosing mode fails EP-59/R20 hiding test")
    finally:
        s3.shutdown()

    # 4. contradictory mode grants a type and no attribute of it
    s4 = serve(port=0, mode="contradictory")
    try:
        code, out = run_pytest({
            "ACCESS_URL": f"{s4.url}/access",
            "ACCESS_CHECK_URL": f"{s4.url}/access/check",
            "DATA_URL": f"{s4.url}/ngsi-ld/v1",
            "FORBIDDEN_TYPE": "ServiceAccount",
            "HIDDEN_ATTR": "stationApiKey",
        })
        if code == 0:
            print("FAIL case 4: contradictory mode passed unexpectedly", file=sys.stderr)
            return 1
        if "test_ep56_a_grant_that_grants_nothing_is_not_a_grant" not in out:
            print(f"FAIL case 4: the consistency test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 4 ok: a grant over no attribute fails the consistency test")
    finally:
        s4.shutdown()

    # 5. check_leaks mode leaks policy in reason on denial
    s5 = serve(port=0, mode="check_leaks")
    try:
        code, out = run_pytest({
            "ACCESS_URL": f"{s5.url}/access",
            "ACCESS_CHECK_URL": f"{s5.url}/access/check",
            "DATA_URL": f"{s5.url}/ngsi-ld/v1",
            "FORBIDDEN_TYPE": "ServiceAccount",
            "HIDDEN_ATTR": "stationApiKey",
        })
        if code == 0:
            print("FAIL case 5: check_leaks mode passed unexpectedly", file=sys.stderr)
            return 1
        if "test_r51_access_check_answers_the_authzen_evaluation_shape" not in out:
            print(f"FAIL case 5: AuthZEN check evaluation test not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 5 ok: check_leaks mode fails AuthZEN evaluation leak test")
    finally:
        s5.shutdown()

    print("ok: access and ODRL suites go red on lossy ODRL, disclosure, contradiction and policy leaks")
    return 0


if __name__ == "__main__":
    sys.exit(main())

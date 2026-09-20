#!/usr/bin/env python3
"""Proof that the LinkML Mapping parity conformance suite goes red on negative cases (T-0079).

Asserts that:
1. both stub engines configured -> the whole suite passes and both golden tests ran;
2. only one engine configured -> test_dm39_dm52_... fails with the message about a parity check that
   proves nothing;
3. the divergent IR engine in place of the good one -> test_dm39_dm52_... fails, and the report names
   both the engine and the qualityBand slot that diverged;
4. a golden expect file edited so it no longer matches what both engines produce -> the same test
   fails and the report shows both engines agreeing against the golden file (this is the case that
   proves the check is not simply comparing an engine with itself);
5. a mapping whose spec.tests is empty -> test_dm39_every_mapping_carries_at_least_one_golden_test
   fails;
6. a missing generated/{name}.ir.json -> test_dm35_dm52_the_compiled_artifacts_are_committed fails;
7. no engine configured at all -> the parity test skips (not passes silently, not fails): assert
   pytest reports it as skipped with the reason naming the three environment variables.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def run_pytest(projects_dir: Path, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    models_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    env["PROJECTS_DIR"] = str(projects_dir)
    for var in ("BENTO_ENGINE_CMD", "GATEWAY_IR_ENGINE_CMD", "MODEL_TOOLS_ENGINE_CMD", "MODEL_TOOLS_CMD"):
        env.pop(var, None)
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(models_dir), "-q", "-p", "no:cacheprovider", "-ra"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined_output = proc.stdout + "\n" + proc.stderr
    return proc.returncode, combined_output


def main() -> int:
    models_dir = Path(__file__).resolve().parent
    fixture_src = models_dir / "fixtures"

    if not fixture_src.exists():
        print(f"FAIL: fixture directory not found at {fixture_src}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # 1. Both stub engines configured -> passes
        f1 = tmp / "case1"
        shutil.copytree(fixture_src, f1)
        b_engine1 = next(f1.rglob("bloblang_engine.py"))
        ir_engine1 = next(f1.rglob("ir_engine.py"))
        env1 = {
            "BENTO_ENGINE_CMD": f"{sys.executable} {b_engine1}",
            "GATEWAY_IR_ENGINE_CMD": f"{sys.executable} {ir_engine1}",
        }
        code, out = run_pytest(f1, extra_env=env1)
        if code != 0:
            print(f"FAIL case 1: fixture failed with both engines:\n{out}", file=sys.stderr)
            return 1
        if "passed" not in out:
            print(f"FAIL case 1: expected passed tests, got:\n{out}", file=sys.stderr)
            return 1
        print("case 1 ok: both stub engines configured, suite passes")

        # 2. Only one engine configured -> fails with message about parity check proving nothing
        f2 = tmp / "case2"
        shutil.copytree(fixture_src, f2)
        ir_engine2 = next(f2.rglob("ir_engine.py"))
        env2 = {"GATEWAY_IR_ENGINE_CMD": f"{sys.executable} {ir_engine2}"}
        code, out = run_pytest(f2, extra_env=env2)
        if code == 0:
            print("FAIL case 2: single engine passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm39_dm52_every_engine_produces_the_expected_payload" not in out:
            print(f"FAIL case 2: parity test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "parity check with one engine proves nothing" not in out:
            print(f"FAIL case 2: expected warning about parity check with one engine, got:\n{out}", file=sys.stderr)
            return 1
        print("case 2 ok: single engine fails parity check")

        # 3. Divergent IR engine -> fails, names engine and qualityBand slot
        f3 = tmp / "case3"
        shutil.copytree(fixture_src, f3)
        b_engine3 = next(f3.rglob("bloblang_engine.py"))
        div_engine3 = next(f3.rglob("divergent_ir_engine.py"))
        env3 = {
            "BENTO_ENGINE_CMD": f"{sys.executable} {b_engine3}",
            "GATEWAY_IR_ENGINE_CMD": f"{sys.executable} {div_engine3}",
        }
        code, out = run_pytest(f3, extra_env=env3)
        if code == 0:
            print("FAIL case 3: divergent IR engine passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm39_dm52_every_engine_produces_the_expected_payload" not in out:
            print(f"FAIL case 3: parity test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "qualityBand" not in out:
            print(f"FAIL case 3: differing slot qualityBand not reported:\n{out}", file=sys.stderr)
            return 1
        if "ir" not in out:
            print(f"FAIL case 3: divergent engine name not reported:\n{out}", file=sys.stderr)
            return 1
        print("case 3 ok: divergent IR engine fails and names qualityBand")

        # 4. Golden expect file edited -> fails, shows both engines agreeing against golden file
        f4 = tmp / "case4"
        shutil.copytree(fixture_src, f4)
        b_engine4 = next(f4.rglob("bloblang_engine.py"))
        ir_engine4 = next(f4.rglob("ir_engine.py"))
        expect_file = next(f4.rglob("bb-good.jsonld"))
        exp_data = json.loads(expect_file.read_text(encoding="utf-8"))
        exp_data["qualityBand"]["value"] = "Z"
        expect_file.write_text(json.dumps(exp_data, indent=2), encoding="utf-8")
        env4 = {
            "BENTO_ENGINE_CMD": f"{sys.executable} {b_engine4}",
            "GATEWAY_IR_ENGINE_CMD": f"{sys.executable} {ir_engine4}",
        }
        code, out = run_pytest(f4, extra_env=env4)
        if code == 0:
            print("FAIL case 4: edited expect file passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm39_dm52_every_engine_produces_the_expected_payload" not in out:
            print(f"FAIL case 4: parity test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "engine 'bloblang' differs from expected" not in out or "engine 'ir' differs from expected" not in out:
            print(f"FAIL case 4: expected both engines to be reported differing from golden file:\n{out}", file=sys.stderr)
            return 1
        if "disagree at" in out:
            print(f"FAIL case 4: engines unexpectedly disagreed with each other:\n{out}", file=sys.stderr)
            return 1
        print("case 4 ok: engines agree with each other but fail against edited expect file")

        # 5. Mapping with spec.tests empty -> test_dm39_every_mapping_carries_at_least_one_golden_test fails
        f5 = tmp / "case5"
        shutil.copytree(fixture_src, f5)
        b_engine5 = next(f5.rglob("bloblang_engine.py"))
        ir_engine5 = next(f5.rglob("ir_engine.py"))
        map_file = next(f5.rglob("sdm-airquality-to-bb.yaml"))
        map_data = yaml.safe_load(map_file.read_text(encoding="utf-8"))
        map_data["spec"]["tests"] = []
        map_file.write_text(yaml.safe_dump(map_data), encoding="utf-8")
        env5 = {
            "BENTO_ENGINE_CMD": f"{sys.executable} {b_engine5}",
            "GATEWAY_IR_ENGINE_CMD": f"{sys.executable} {ir_engine5}",
        }
        code, out = run_pytest(f5, extra_env=env5)
        if code == 0:
            print("FAIL case 5: mapping with empty tests passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm39_every_mapping_carries_at_least_one_golden_test" not in out:
            print(f"FAIL case 5: golden test presence test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 5 ok: mapping without golden tests fails test_dm39")

        # 6. Missing generated/{name}.ir.json -> test_dm35_dm52_the_compiled_artifacts_are_committed fails
        f6 = tmp / "case6"
        shutil.copytree(fixture_src, f6)
        b_engine6 = next(f6.rglob("bloblang_engine.py"))
        ir_engine6 = next(f6.rglob("ir_engine.py"))
        ir_file = next(f6.rglob("sdm-airquality-to-bb.ir.json"))
        ir_file.unlink()
        env6 = {
            "BENTO_ENGINE_CMD": f"{sys.executable} {b_engine6}",
            "GATEWAY_IR_ENGINE_CMD": f"{sys.executable} {ir_engine6}",
        }
        code, out = run_pytest(f6, extra_env=env6)
        if code == 0:
            print("FAIL case 6: missing IR file passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm35_dm52_the_compiled_artifacts_are_committed" not in out:
            print(f"FAIL case 6: compiled artifacts test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 6 ok: missing compiled IR artifact fails test_dm35_dm52")

        # 7. No engine configured -> skips and reason names the three environment variables
        f7 = tmp / "case7"
        shutil.copytree(fixture_src, f7)
        code, out = run_pytest(f7, extra_env={})
        if code != 0:
            print(f"FAIL case 7: suite unexpectedly failed with no engines configured:\n{out}", file=sys.stderr)
            return 1
        if "test_dm39_dm52_every_engine_produces_the_expected_payload" in out and "FAILED" in out:
            print(f"FAIL case 7: parity test failed instead of skipping:\n{out}", file=sys.stderr)
            return 1
        for var in ("BENTO_ENGINE_CMD", "GATEWAY_IR_ENGINE_CMD", "MODEL_TOOLS_ENGINE_CMD"):
            if var not in out:
                print(f"FAIL case 7: expected {var} in skip reason, got:\n{out}", file=sys.stderr)
                return 1
        print("case 7 ok: unconfigured engines cleanly skip parity test naming all three env vars")

    print(
        "ok: negative cases go red on a single engine, divergent engine, edited expect file, "
        "missing golden tests, missing compiled IR, and skip cleanly on unconfigured engines"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

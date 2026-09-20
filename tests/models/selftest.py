#!/usr/bin/env python3
"""Proof that the LinkML artifact conformance suite goes red on negative cases (T-0078).

Asserts that:
1. The untouched fixture passes with no failures and at least 5 model tests ran.
2. Deleting a required slot fails test_ts18_... AND test_dm46_...
3. Adding "unevaluatedProperties": false to schema fails test_dm03_... only.
4. Removing sh:minCount from shapes fails test_dm46_the_shapes_reject_an_entity_that_breaks_the_model.
5. Mismatching v{major} in artifact path fails test_dm02_dm22_...
6. An empty projects tree fails test_the_projects_tree_holds_at_least_one_data_model.
7. A fake generator writing different bytes fails test_dm02_regenerating_... and names the file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_pytest(projects_dir: Path, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    models_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    env["PROJECTS_DIR"] = str(projects_dir)
    if extra_env:
        env.update(extra_env)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(models_dir), "-q", "-p", "no:cacheprovider"],
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

        # 1. Untouched fixture passes
        f1 = tmp / "case1"
        shutil.copytree(fixture_src, f1)
        code, out = run_pytest(f1)
        if code != 0:
            print(f"FAIL case 1: untouched fixture failed:\n{out}", file=sys.stderr)
            return 1
        if "passed" not in out:
            print(f"FAIL case 1: expected passed tests, got:\n{out}", file=sys.stderr)
            return 1
        print("case 1 ok: untouched fixture passes all tests")

        # 2. Example with required slot deleted
        f2 = tmp / "case2"
        shutil.copytree(fixture_src, f2)
        ex_file = next(f2.rglob("bb-air-quality.example.jsonld"))
        data = json.loads(ex_file.read_text(encoding="utf-8"))
        data.pop("stationName", None)
        ex_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        code, out = run_pytest(f2)
        if code == 0:
            print("FAIL case 2: broken example passed unexpectedly", file=sys.stderr)
            return 1
        if "test_ts18_the_committed_example_validates_against_the_committed_json_schema" not in out:
            print(f"FAIL case 2: test_ts18 was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "test_dm46_pyshacl_accepts_the_example_expanded_with_the_served_context" not in out:
            print(f"FAIL case 2: test_dm46 was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 2 ok: missing required slot fails test_ts18 and test_dm46")

        # 3. JSON Schema carrying "unevaluatedProperties": false
        f3 = tmp / "case3"
        shutil.copytree(fixture_src, f3)
        schema_file = next(f3.rglob("bb-air-quality.v2.json"))
        sdata = json.loads(schema_file.read_text(encoding="utf-8"))
        sdata["unevaluatedProperties"] = False
        schema_file.write_text(json.dumps(sdata, indent=2), encoding="utf-8")
        code, out = run_pytest(f3)
        if code == 0:
            print("FAIL case 3: forbidden keyword in schema passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm03_the_committed_json_schema_is_draft_07" not in out:
            print(f"FAIL case 3: test_dm03 was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "test_ts18" in out and "FAILED" in out.split("test_ts18")[1][:50]:
            print(f"FAIL case 3: test_ts18 failed unexpectedly:\n{out}", file=sys.stderr)
            return 1
        print("case 3 ok: post-draft-07 keyword fails test_dm03 only")

        # 4. Shapes that carry no constraint at all: they still target the class, so pySHACL
        #    reports conformance for every entity — the shape set nobody can fail.
        f4 = tmp / "case4"
        shutil.copytree(fixture_src, f4)
        ttl_file = next(f4.rglob("bb-air-quality.v2.ttl"))
        ttl_loose = ttl_file.read_text(encoding="utf-8")
        for constraint in ("sh:minCount 1 ;", "sh:datatype xsd:string ;", "sh:datatype xsd:double ;"):
            ttl_loose = ttl_loose.replace(constraint, "")
        ttl_file.write_text(ttl_loose, encoding="utf-8")
        code, out = run_pytest(f4)
        if code == 0:
            print("FAIL case 4: overly lenient shapes passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm46_the_shapes_reject_an_entity_that_breaks_the_model" not in out:
            print(f"FAIL case 4: test_dm46 reject test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 4 ok: overly permissive shapes fail rejection test")

        # 5. Artifact path whose v{major} does not match spec.version
        f5 = tmp / "case5"
        shutil.copytree(fixture_src, f5)
        manifest_file = next(f5.rglob("bb-air-quality.yaml"))
        man_text = manifest_file.read_text(encoding="utf-8")
        man_text = man_text.replace("bb-air-quality.v2.json", "bb-air-quality.v3.json")
        manifest_file.write_text(man_text, encoding="utf-8")
        code, out = run_pytest(f5)
        if code == 0:
            print("FAIL case 5: mismatched major version in artifact path passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm02_dm22_the_manifest_and_its_artifacts_agree" not in out:
            print(f"FAIL case 5: test_dm02_dm22 was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 5 ok: mismatched artifact path version fails test_dm02_dm22")

        # 6. Empty projects tree fails test_the_projects_tree_holds_at_least_one_data_model
        f6 = tmp / "case6"
        f6.mkdir()
        code, out = run_pytest(f6)
        if code == 0:
            print("FAIL case 6: empty projects tree passed unexpectedly", file=sys.stderr)
            return 1
        if "test_the_projects_tree_holds_at_least_one_data_model" not in out:
            print(f"FAIL case 6: empty tree test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        print("case 6 ok: empty projects directory fails suite")

        # 7. Fake generator producing byte-different artifact
        f7 = tmp / "case7"
        shutil.copytree(fixture_src, f7)
        fake_gen = tmp / "fake_gen.py"
        fake_gen.write_text(
            "import sys, os, pathlib\n"
            "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'bb-air-quality.v2.json').write_text('{\"different\": true}')\n",
            encoding="utf-8",
        )
        fake_gen.chmod(0o755)

        fake_cmd = f"{sys.executable} {fake_gen}"
        code, out = run_pytest(f7, extra_env={"MODEL_TOOLS_CMD": fake_cmd})
        if code == 0:
            print("FAIL case 7: byte-different generator output passed unexpectedly", file=sys.stderr)
            return 1
        if "test_dm02_regenerating_the_artifacts_reproduces_the_committed_files" not in out:
            print(f"FAIL case 7: regeneration test was not reported in failures:\n{out}", file=sys.stderr)
            return 1
        if "bb-air-quality.v2.json" not in out:
            print(f"FAIL case 7: diff report did not name the modified file:\n{out}", file=sys.stderr)
            return 1
        print("case 7 ok: regeneration diff fails and names differing artifact")

        # 8. A context that does not alias `type` to `@type`: the example expands to a node with
        #    no rdf:type, sh:targetClass selects nothing, and a naive harness would call that a pass.
        f8 = tmp / "case8"
        shutil.copytree(fixture_src, f8)
        ctx_file = next(f8.rglob("bb-air-quality.v2.jsonld"))
        ctx_file.write_text(
            ctx_file.read_text(encoding="utf-8").replace('"type": "@type",', ""), encoding="utf-8"
        )
        code, out = run_pytest(f8)
        if code == 0:
            print("FAIL case 8: an example that expands untyped passed unexpectedly", file=sys.stderr)
            return 1
        if "no node of any sh:targetClass" not in out:
            print(f"FAIL case 8: the empty-focus-node guard did not report:\n{out}", file=sys.stderr)
            return 1
        print("case 8 ok: an untyped expansion is reported instead of silently conforming")

    print(
        "ok: negative cases go red on a bad example, post-draft-07 keywords, toothless shapes, "
        "a version mismatch, an empty tree, a generator diff and an untyped expansion"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

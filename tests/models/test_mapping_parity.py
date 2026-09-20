"""Pytest suite for LinkML Mapping parity conformance (T-0079).

Verifies DM-35, DM-39, DM-52.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from parity import (
    EngineRun,
    Mapping,
    discover,
    divergences,
    manifest_violations,
    run_engine,
)


def _models_root() -> Path:
    env_dir = os.environ.get("PROJECTS_DIR")
    if not env_dir:
        pytest.skip("PROJECTS_DIR environment variable is not set")
    p = Path(env_dir).resolve()
    if not p.is_dir():
        pytest.skip(f"PROJECTS_DIR does not exist or is not a directory: {p}")
    return p


def _all_mappings() -> list[Mapping]:
    env_dir = os.environ.get("PROJECTS_DIR")
    if not env_dir:
        return []
    p = Path(env_dir).resolve()
    return discover(p) if p.is_dir() else []


MAPPINGS = _all_mappings()


@pytest.mark.parametrize("mapping", MAPPINGS, ids=lambda m: m.name)
def test_dm39_every_mapping_carries_at_least_one_golden_test(mapping: Mapping) -> None:
    violations = manifest_violations(mapping)
    assert not violations, (
        f"Mapping manifest violations for {mapping.name} (DM-39):\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("mapping", MAPPINGS, ids=lambda m: m.name)
def test_dm35_dm52_the_compiled_artifacts_are_committed(mapping: Mapping) -> None:
    assert mapping.bloblang.is_file(), f"Missing Bloblang artifact: {mapping.bloblang}"
    assert mapping.bloblang.stat().st_size > 0, f"Bloblang artifact is empty: {mapping.bloblang}"
    assert mapping.ir.is_file(), f"Missing gateway IR artifact: {mapping.ir}"
    assert mapping.ir.stat().st_size > 0, f"Gateway IR artifact is empty: {mapping.ir}"

    try:
        ir_data = json.loads(mapping.ir.read_text(encoding="utf-8"))
    except Exception as e:
        pytest.fail(f"Gateway IR is not valid JSON ({mapping.ir}): {e}")

    version_field = ir_data.get("irVersion") or ir_data.get("version")
    assert version_field, (
        f"Gateway IR artifact {mapping.ir.name} must declare a version field ('irVersion' or 'version')"
    )


@pytest.mark.parametrize("mapping", MAPPINGS, ids=lambda m: m.name)
def test_dm35_recompiling_the_mapping_reproduces_the_committed_artifacts(
    mapping: Mapping, tmp_path: Path
) -> None:
    cmd_raw = os.environ.get("MODEL_TOOLS_CMD")
    if not cmd_raw:
        pytest.skip("MODEL_TOOLS_CMD environment variable is not set")

    cmd = cmd_raw.split() + ["compile", str(mapping.manifest), "--out", str(tmp_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode == 0, (
        f"Model Tools compile failed with exit code {res.returncode}: {res.stderr.strip()}"
    )

    for art_path in (mapping.bloblang, mapping.ir):
        recompiled = tmp_path / art_path.name
        assert recompiled.is_file(), f"Recompiled artifact not produced: {art_path.name}"
        assert art_path.read_bytes() == recompiled.read_bytes(), (
            f"Recompiled artifact differs from committed: {art_path.name}"
        )


@pytest.mark.parametrize("mapping", MAPPINGS, ids=lambda m: m.name)
def test_dm39_dm52_every_engine_produces_the_expected_payload(mapping: Mapping) -> None:
    bento_cmd = os.environ.get("BENTO_ENGINE_CMD")
    ir_cmd = os.environ.get("GATEWAY_IR_ENGINE_CMD")
    model_tools_cmd = os.environ.get("MODEL_TOOLS_ENGINE_CMD")

    if not bento_cmd and not ir_cmd and not model_tools_cmd:
        pytest.skip(
            "no mapping engines configured: set at least two of BENTO_ENGINE_CMD, "
            "GATEWAY_IR_ENGINE_CMD, MODEL_TOOLS_ENGINE_CMD"
        )

    configured: dict[str, tuple[list[str], Path]] = {}
    if bento_cmd:
        configured["bloblang"] = (bento_cmd.split(), mapping.bloblang)
    if ir_cmd:
        configured["ir"] = (ir_cmd.split(), mapping.ir)
    if model_tools_cmd:
        configured["model-tools"] = (model_tools_cmd.split(), mapping.manifest)

    assert mapping.tests, f"Mapping {mapping.name} has no golden tests defined"

    all_divergences: list[str] = []

    for input_path, expect_path in mapping.tests:
        assert input_path.is_file(), f"Test input file missing: {input_path}"
        assert expect_path.is_file(), f"Test expect file missing: {expect_path}"

        expected_data = json.loads(expect_path.read_text(encoding="utf-8"))

        runs: dict[str, EngineRun] = {}
        for engine_name, (cmd, artifact) in configured.items():
            runs[engine_name] = run_engine(cmd, artifact, input_path)

        divs = divergences(runs, expected_data)
        for d in divs:
            all_divergences.append(f"[{input_path.name} -> {expect_path.name}] {d}")

    assert not all_divergences, (
        f"Mapping engine divergences detected for {mapping.name} (DM-39, DM-52):\n"
        + "\n".join(all_divergences)
    )

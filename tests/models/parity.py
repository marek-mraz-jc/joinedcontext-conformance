"""Pure functions for discovering and validating LinkML Mapping parity (T-0079).

Verifies DM-35, DM-39, and DM-52.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Mapping:
    manifest: Path
    name: str
    source: str          # "name@version" of the source DataModel
    target: str
    bloblang: Path       # generated/{name}.blobl beside the manifest
    ir: Path             # generated/{name}.ir.json beside the manifest
    tests: tuple[tuple[Path, Path], ...]   # (input, expect) pairs, resolved
    missing: tuple[str, ...]


@dataclass(frozen=True)
class EngineRun:
    engine: str
    ok: bool
    payload: Any
    error: str = ""


def discover(root: Path) -> list[Mapping]:
    """Discover all kind: Mapping manifests under root."""
    if not root.is_dir():
        return []

    mappings: list[Mapping] = []
    candidates = sorted(list(root.rglob("*.yaml")) + list(root.rglob("*.yml")))

    for path in candidates:
        try:
            content = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(content, dict) or content.get("kind") != "Mapping":
            continue

        metadata = content.get("metadata") or {}
        spec = content.get("spec") or {}

        name = str(metadata.get("name") or path.stem)
        manifest_dir = path.parent

        spec_source = spec.get("source") or {}
        spec_target = spec.get("target") or {}

        src_name = str(spec_source.get("name") or "")
        src_ver = str(spec_source.get("version") or "")
        source_str = f"{src_name}@{src_ver}" if (src_name or src_ver) else ""

        tgt_name = str(spec_target.get("name") or "")
        tgt_ver = str(spec_target.get("version") or "")
        target_str = f"{tgt_name}@{tgt_ver}" if (tgt_name or tgt_ver) else ""

        bloblang_path = (manifest_dir / "generated" / f"{name}.blobl").resolve()
        ir_path = (manifest_dir / "generated" / f"{name}.ir.json").resolve()

        missing: list[str] = []
        if not bloblang_path.exists():
            missing.append(f"compiled Bloblang artifact not found: {bloblang_path}")
        if not ir_path.exists():
            missing.append(f"compiled gateway IR artifact not found: {ir_path}")

        resolved_tests: list[tuple[Path, Path]] = []
        raw_tests = spec.get("tests")
        if isinstance(raw_tests, list):
            for idx, t in enumerate(raw_tests):
                if isinstance(t, dict):
                    in_raw = t.get("input")
                    exp_raw = t.get("expect")
                    if in_raw and exp_raw:
                        in_path = (manifest_dir / str(in_raw)).resolve()
                        exp_path = (manifest_dir / str(exp_raw)).resolve()
                        resolved_tests.append((in_path, exp_path))
                        if not in_path.exists():
                            missing.append(f"test[{idx}] input file not found: {in_path}")
                        if not exp_path.exists():
                            missing.append(f"test[{idx}] expect file not found: {exp_path}")
                    else:
                        missing.append(f"test[{idx}] missing input or expect attribute")

        mappings.append(
            Mapping(
                manifest=path.resolve(),
                name=name,
                source=source_str,
                target=target_str,
                bloblang=bloblang_path,
                ir=ir_path,
                tests=tuple(resolved_tests),
                missing=tuple(missing),
            )
        )

    return mappings


def manifest_violations(mapping: Mapping) -> list[str]:
    """Check DM-39 golden tests declaration and missing files in Mapping manifest."""
    violations: list[str] = []

    if not mapping.tests:
        violations.append(
            f"Mapping '{mapping.name}' spec.tests is missing or empty (must carry at least one golden test)"
        )

    for m in mapping.missing:
        violations.append(f"missing referenced file: {m}")

    if (
        not mapping.source
        or "@" not in mapping.source
        or not mapping.source.split("@")[0]
        or not mapping.source.split("@")[1]
    ):
        violations.append(
            f"Mapping '{mapping.name}' spec.source must declare both name and version (got '{mapping.source}')"
        )

    if (
        not mapping.target
        or "@" not in mapping.target
        or not mapping.target.split("@")[0]
        or not mapping.target.split("@")[1]
    ):
        violations.append(
            f"Mapping '{mapping.name}' spec.target must declare both name and version (got '{mapping.target}')"
        )

    return violations


def canonical(payload: Any) -> str:
    """Serialize payload to canonical JSON for comparison (DM-39).

    Two engines may serialize differently (key ordering, whitespace);
    canonicalizing guarantees comparison is strictly on payload semantics.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def run_engine(
    command: list[str], artifact: Path, input_path: Path, timeout: int = 60
) -> EngineRun:
    """Run an engine command against an artifact and input path, capturing JSON stdout."""
    engine_name = Path(command[0]).name if command else "unknown"
    cmd = command + [str(artifact), str(input_path)]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return EngineRun(
            engine=engine_name,
            ok=False,
            payload=None,
            error=f"timeout after {timeout}s",
        )
    except Exception as e:
        return EngineRun(
            engine=engine_name,
            ok=False,
            payload=None,
            error=f"failed to execute: {e}",
        )

    if res.returncode != 0:
        last_stderr = (
            res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "no stderr"
        )
        return EngineRun(
            engine=engine_name,
            ok=False,
            payload=None,
            error=f"exit code {res.returncode}: {last_stderr}",
        )

    try:
        payload = json.loads(res.stdout)
    except Exception as e:
        return EngineRun(
            engine=engine_name,
            ok=False,
            payload=None,
            error=f"invalid JSON stdout: {e}",
        )

    return EngineRun(engine=engine_name, ok=True, payload=payload, error="")


def find_first_diff(got: Any, expected: Any, path: str = "") -> str:
    """Walk both JSON structures and return the first differing slot path and values."""
    if type(got) is not type(expected):
        p = path or "<root>"
        return f"{p}: expected {type(expected).__name__} <{expected}>, got {type(got).__name__} <{got}>"

    if isinstance(got, dict):
        got_keys = set(got.keys())
        exp_keys = set(expected.keys())
        if got_keys != exp_keys:
            extra = got_keys - exp_keys
            missing = exp_keys - got_keys
            parts: list[str] = []
            if extra:
                parts.append(f"extra keys {sorted(extra)}")
            if missing:
                parts.append(f"missing keys {sorted(missing)}")
            p = path or "<root>"
            return f"{p}: {', '.join(parts)}"

        for k in sorted(got.keys()):
            child_path = f"{path}.{k}" if path else k
            if got[k] != expected[k]:
                return find_first_diff(got[k], expected[k], child_path)

    elif isinstance(got, list):
        if len(got) != len(expected):
            p = path or "<root>"
            return f"{p}: expected length {len(expected)}, got {len(got)}"
        for idx, (item_g, item_e) in enumerate(zip(got, expected)):
            child_path = f"{path}[{idx}]"
            if item_g != item_e:
                return find_first_diff(item_g, item_e, child_path)

    elif got != expected:
        p = path or "<root>"
        return f"{p}: expected <{expected}>, got <{got}>"

    return ""


def divergences(runs: dict[str, EngineRun], expected: dict[str, Any]) -> list[str]:
    """Evaluate parity across engine executions against expected payload (DM-39, DM-52)."""
    violations: list[str] = []

    successful_runs = {k: r for k, r in runs.items() if r.ok and r.payload is not None}
    if len(successful_runs) < 2:
        violations.append(
            f"fewer than two engines produced a payload: a parity check with one engine proves nothing "
            f"({len(successful_runs)} produced a payload: {sorted(successful_runs.keys())})"
        )

    for name, run in runs.items():
        if not run.ok:
            violations.append(f"engine '{name}' failed: {run.error}")

    expected_canon = canonical(expected)
    for name, run in successful_runs.items():
        if canonical(run.payload) != expected_canon:
            diff = find_first_diff(run.payload, expected)
            violations.append(f"engine '{name}' differs from expected at {diff}")

    successful_names = sorted(successful_runs.keys())
    for i in range(len(successful_names)):
        for j in range(i + 1, len(successful_names)):
            e1 = successful_names[i]
            e2 = successful_names[j]
            if canonical(runs[e1].payload) != canonical(runs[e2].payload):
                diff = find_first_diff(runs[e1].payload, runs[e2].payload)
                violations.append(f"engines '{e1}' and '{e2}' disagree at {diff}")

    return violations

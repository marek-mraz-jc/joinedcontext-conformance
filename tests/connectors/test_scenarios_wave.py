"""Every scenarios-wave/<task>.yaml (T-6234..T-6253) runs through `claude_code.py run --scenarios`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import claude_code

WAVE = sorted((Path(__file__).parent / "scenarios-wave").glob("T-*.yaml"))


@pytest.mark.parametrize("source", WAVE, ids=[p.stem for p in WAVE])
def test_a_wave_scenario_is_runnable_and_tabled(source):
    steps = claude_code.scenarios(None, source)
    assert steps, f"{source.name} holds no connector step"
    ids = [s["id"] for s in steps]
    assert len(ids) == len(set(ids)), f"{source.name} repeats a step id"
    for step in steps:
        # `table` reads only S-*.jsonl, and a step without `{project}` filled in would name no project.
        assert step["id"].startswith("S-"), step["id"]
        assert "{project}" not in claude_code.prompt(step, "t6025-full")
        assert step.get("expect") and step.get("deny"), (
            f"{step['id']} has no expect or deny line"
        )

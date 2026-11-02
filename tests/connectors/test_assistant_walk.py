"""What assistant_walk.py keeps of a run and the table it prints from saved files (T-5962)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import assistant_walk


def test_a_long_tool_output_is_cut_and_a_token_scrubbed():
    long = "x" * 5000
    events = [
        {"kind": "tool", "payload": {"tool": "jc_a", "output": long}},
        {
            "kind": "tool",
            "payload": {
                "tool": "jc_b",
                "output": {"note": "Bearer abcdefghijklmnopqrstuvwx"},
            },
        },
    ]
    out = assistant_walk.kept(events)
    assert len(out[0]["payload"]["output"]) == assistant_walk.KEEP + 3
    assert "abcdefghijklmnopqrstuvwx" not in json.dumps(out)


def test_the_table_reads_saved_files_and_the_written_verdicts(tmp_path):
    record = {
        "id": "S-09",
        "seconds": 12,
        "usd": 0.01,
        "events": [{"kind": "tool", "payload": {}}, {"kind": "usage", "payload": {}}],
    }
    (tmp_path / "S-09.json").write_text(json.dumps(record))
    (tmp_path / "S-10.json").write_text(json.dumps(record | {"id": "S-10"}))
    (tmp_path / "verdicts.yaml").write_text("S-09: {pass: true, note: opened chg-1}\n")
    table = assistant_walk.table(tmp_path)
    assert "| S-09 | pass | 1 | 12 | 0.01 | opened chg-1 |" in table
    assert "| S-10 | ? | 1 | 12 | 0.01 |  |" in table

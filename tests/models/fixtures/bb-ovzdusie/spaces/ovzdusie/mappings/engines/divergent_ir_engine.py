#!/usr/bin/env python3
"""Divergent gateway IR engine for selftest negative checks (T-0079). Ignores valueMap."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: divergent_ir_engine.py <ir.json> <input.jsonld>", file=sys.stderr)
        return 2

    ir_path = Path(sys.argv[1])
    input_path = Path(sys.argv[2])

    ir_spec = json.loads(ir_path.read_text(encoding="utf-8"))
    source = json.loads(input_path.read_text(encoding="utf-8"))

    target: dict = {}
    if "id" in source:
        target["id"] = source["id"]
    if "type" in source:
        target["type"] = ir_spec.get("targetClass", source["type"])

    for slot in ir_spec.get("slots", []):
        tgt_name = slot["target"]
        src_name = slot["from"]
        if src_name not in source:
            continue
        # Deliberately ignore valueMap and copy raw source (simulates engine bug)
        target[tgt_name] = source[src_name]

    print(json.dumps(target, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

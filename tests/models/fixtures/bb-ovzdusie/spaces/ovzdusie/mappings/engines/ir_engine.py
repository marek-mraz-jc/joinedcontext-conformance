#!/usr/bin/env python3
"""Minimal gateway mapping IR stub engine for conformance testing (DM-52)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: ir_engine.py <ir.json> <input.jsonld>", file=sys.stderr)
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
        val = source[src_name]
        vmap = slot.get("valueMap")
        if vmap and isinstance(val, dict) and "value" in val:
            mapped_val = vmap.get(val["value"], val["value"])
            target[tgt_name] = {**val, "value": mapped_val}
        elif vmap and not isinstance(val, dict):
            target[tgt_name] = vmap.get(val, val)
        else:
            target[tgt_name] = val

    print(json.dumps(target, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

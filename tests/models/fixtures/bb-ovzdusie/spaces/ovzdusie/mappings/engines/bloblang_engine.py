#!/usr/bin/env python3
"""Minimal Bloblang mapping stub engine for conformance testing (DM-39)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: bloblang_engine.py <mapping.blobl> <input.jsonld>", file=sys.stderr)
        return 2

    blobl_path = Path(sys.argv[1])
    input_path = Path(sys.argv[2])

    blobl_text = blobl_path.read_text(encoding="utf-8")
    source = json.loads(input_path.read_text(encoding="utf-8"))

    target: dict[str, Any] = {}

    def set_path(d: dict[str, Any], path: str, val: Any) -> None:
        parts = path.split(".")
        cur = d
        for part in parts[:-1]:
            if part not in cur or not isinstance(cur[part], dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = val

    def get_path(d: dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        cur = d
        for part in parts:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    match_pattern = re.compile(
        r"root\.([\w.]+)\s*=\s*match\s+this\.([\w.]+)\s*\{([^}]+)\}",
        re.MULTILINE | re.DOTALL,
    )
    for match in match_pattern.finditer(blobl_text):
        target_field = match.group(1)
        src_field = match.group(2)
        body = match.group(3)
        vmap = {}
        for line in body.splitlines():
            m = re.search(r'["\']([^"\']+)["\']\s*=>\s*["\']([^"\']+)["\']', line)
            if m:
                vmap[m.group(1)] = m.group(2)
        src_val = get_path(source, src_field)
        mapped = vmap.get(src_val, src_val)
        set_path(target, target_field, mapped)

    text_without_match = match_pattern.sub("", blobl_text)

    literal_pattern = re.compile(r'root\.([\w.]+)\s*=\s*"([^"]*)"')
    for match in literal_pattern.finditer(text_without_match):
        set_path(target, match.group(1), match.group(2))

    assign_pattern = re.compile(r"root\.([\w.]+)\s*=\s*this\.([\w.]+)")
    for match in assign_pattern.finditer(text_without_match):
        target_field = match.group(1)
        src_field = match.group(2)
        val = get_path(source, src_field)
        if val is not None:
            set_path(target, target_field, val)

    print(json.dumps(target, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

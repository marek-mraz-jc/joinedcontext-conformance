#!/usr/bin/env python3
"""The T-6024 table from a connector's audit rows (API/14, AG-147).

    python3 tests/connectors/audit_table.py --calls calls.json [--checklist claude_ai_checklist.yaml] [--gap 60]

`calls.json` is the answer of `GET /api/v1/connectors/{id}/activity` (`{"items": [...]}`, newest
first), saved by the connector's owner signed in to the Portal; it holds no token or input value.
The rows are split into prompts where the connector was idle longer than `--gap` seconds; a run of
protocol calls alone (claude.ai listing tools when the connector is added) is no prompt. The
split must give one group per checklist prompt, in order, or the script names the groups it found.

A prompt passes when every row came from the checklist's client, each `tools` entry has an allowed
call and each `refused` entry was refused and never allowed. The end state (`expect`) stays a
person's reading in the Portal; this script judges the calls. Exit 1 when any prompt fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PROTOCOL = {
    "initialize",
    "ping",
    "tools/list",
    "notifications/initialized",
    "resources/list",
    "prompts/list",
}


def _at(row: dict) -> datetime:
    return datetime.fromisoformat(row["at"])


def split(rows: list[dict], gap: float) -> list[list[dict]]:
    """Oldest first, a new group after `gap` idle seconds; groups of protocol calls alone dropped."""
    groups: list[list[dict]] = []
    end = None
    for row in sorted(rows, key=_at):
        start = _at(row)
        if end is None or (start - end).total_seconds() > gap:
            groups.append([])
        groups[-1].append(row)
        done = start + timedelta(milliseconds=row.get("durationMs", 0))
        end = done if end is None else max(end, done)
    return [g for g in groups if any(r["tool"] not in PROTOCOL for r in g)]


def judge(prompt: dict, rows: list[dict], client: str) -> dict:
    allowed = {r["tool"] for r in rows if r["verdict"] == "allowed"}
    refused = [r for r in rows if r["verdict"].startswith("refused")]
    why = []
    others = sorted({r.get("client") or "none" for r in rows} - {client})
    if others:
        why.append("client " + ", ".join(others))
    why += [f"{t} not allowed" for t in prompt.get("tools", []) if t not in allowed]
    for tool in prompt.get("refused", []):
        if tool in allowed:
            why.append(f"{tool} allowed")
        elif not any(r["tool"] == tool for r in refused):
            why.append(f"{tool} not called")
    called: dict[str, int] = {}
    for r in rows:
        if r["tool"] not in PROTOCOL:
            called[r["tool"]] = called.get(r["tool"], 0) + 1
    first = _at(rows[0])
    last = max(_at(r) + timedelta(milliseconds=r.get("durationMs", 0)) for r in rows)
    return {
        "id": prompt["id"],
        "segment": prompt["segment"],
        "tools": ", ".join(f"{t} x{n}" if n > 1 else t for t, n in called.items()),
        "refused": ", ".join(
            f"{r['tool']} ({r['verdict'][len('refused: ') :] or 'refused'})"
            for r in refused
        ),
        "pass": not why,
        "why": "; ".join(why),
        "seconds": round((last - first).total_seconds(), 1),
    }


def table(checklist: dict, rows: list[dict], gap: float) -> tuple[str, bool]:
    prompts = checklist["prompts"]
    groups = split(rows, gap)
    if len(groups) != len(prompts):
        found = "; ".join(f"{_at(g[0]).isoformat()} {len(g)} rows" for g in groups)
        raise SystemExit(
            f"{len(groups)} prompt groups for {len(prompts)} prompts at --gap {gap}: {found}"
        )
    results = [judge(p, g, checklist["client"]) for p, g in zip(prompts, groups)]
    lines = [
        "| prompt | segment | tools called | refused calls | pass | seconds |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "pass" if r["pass"] else f"fail: {r['why']}"
        lines.append(
            f"| {r['id']} | {r['segment']} | {r['tools'] or '-'} | {r['refused'] or '-'} | {verdict} | {r['seconds']} |"
        )
    return "\n".join(lines) + "\n", all(r["pass"] for r in results)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--calls", type=Path, required=True)
    ap.add_argument("--checklist", type=Path, default=HERE / "claude_ai_checklist.yaml")
    ap.add_argument(
        "--gap", type=float, default=60.0, help="idle seconds that start a new prompt"
    )
    args = ap.parse_args(argv)
    checklist = yaml.safe_load(args.checklist.read_text(encoding="utf-8"))
    rows = json.loads(args.calls.read_text(encoding="utf-8"))["items"]
    text, ok = table(checklist, rows, args.gap)
    sys.stdout.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

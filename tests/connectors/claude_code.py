"""Connector scenarios through Claude Code, headless, and the table of what each run cost (T-6025).

`run` starts one fresh `claude -p` session per scenario of tasks/connector-scenarios.yaml (S-01..S-50,
the segments a connector serves), with only the MCP servers of `--mcp-config` and no built-in tool,
and keeps each session's stream-json, scrubbed, as `<out>/<id>.jsonl`. `baseline` records one session
without any MCP server, so the table can tell the tool definitions' share of the first prompt. `table`
reads the saved files back, adds the written verdicts of `<out>/verdicts.yaml` and prints the markdown
table: rerunning it over the same files prints the same table.

    claude_code.py run --mcp-config cfg.json --out DIR [--ids S-01,S-07] [--max-turns 12] [--model M]
    claude_code.py baseline --out DIR
    claude_code.py table DIR

verdicts.yaml maps a scenario id to `{pass: true|false, class: <one of CLASSES>, note: text}`, a
person's (or a reviewer's) reading of the answer against the scenario's expect and deny lines.

Claude Code signs in to an MCP server itself (`claude mcp add --transport http`, then /mcp): no token
passes through this script. The transcripts still go through `scrub` before they touch the disk.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

SCENARIOS = Path("/workspace/tasks/connector-scenarios.yaml")

CLASSES = (
    "missing tool",
    "wrong tool chosen",
    "argument refused by schema",
    "answer too large",
    "unclear error",
    "auth",
)

#: An answer past this many bytes is about 10k tokens of the model's context for one call.
LARGE_ANSWER = 40_000

_SECRETS = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "***"),
    (re.compile(r'(?i)(authorization\\?"?\s*[:=]\s*\\?"?)[^"\\\s,}]+'), r"\1***"),
    (re.compile(r'(?i)(mcp-session-id\\?"?\s*[:=]\s*\\?"?)[^"\\\s,}]+'), r"\1***"),
)


def scrub(text: str) -> str:
    """Replace bearer tokens, JWTs, Authorization values and MCP session ids with ***."""
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


@dataclass
class Call:
    tool: str
    arg_bytes: int
    answer_bytes: int = 0
    refused: bool = False
    ms: int = 0
    answer_head: str = ""


@dataclass
class Run:
    scenario: str
    model: str = ""
    tools_offered: int = 0
    start_tokens: int = 0
    calls: list[Call] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    is_error: bool = False
    answer: str = ""

    @property
    def largest_answer(self) -> int:
        return max((c.answer_bytes for c in self.calls), default=0)

    def hint(self) -> str:
        """A failure class the transcript itself shows, for the reader of a failed row."""
        heads = " ".join(c.answer_head for c in self.calls if c.refused)
        if re.search(r"(?i)sign in|unauthori[sz]ed|forbidden|\b40[13]\b", heads):
            return "auth"
        if re.search(
            r"(?i)schema|invalid (argument|param)|unknown (argument|field)|-32602",
            heads,
        ):
            return "argument refused by schema"
        if self.largest_answer > LARGE_ANSWER:
            return "answer too large"
        if not self.calls:
            return "missing tool"
        return "unclear error" if heads else ""


def _prompt_tokens(usage: dict) -> int:
    return sum(
        int(usage.get(k) or 0)
        for k in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return json.dumps(content)


def _ms(start: str, end: str) -> int:
    return int(
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        * 1000
    )


def parse(lines: list[str], scenario: str) -> Run:
    """Read one session's stream-json lines into a Run."""
    run = Run(scenario)
    pending: dict[str, tuple[Call, str]] = {}
    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            run.model = event.get("model", "")
            run.tools_offered = sum(
                1 for t in event.get("tools", []) if t.startswith("mcp__")
            )
        elif kind == "assistant":
            message = event["message"]
            if not run.start_tokens:
                run.start_tokens = _prompt_tokens(message.get("usage") or {})
            for part in message.get("content", []):
                if part.get("type") == "tool_use":
                    args = json.dumps(
                        part.get("input", {}), separators=(",", ":"), ensure_ascii=False
                    )
                    call = Call(part["name"], len(args.encode()))
                    run.calls.append(call)
                    pending[part["id"]] = (call, event.get("timestamp", ""))
        elif kind == "user":
            for part in event["message"].get("content", []):
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                call, started = pending.pop(part["tool_use_id"], (None, ""))
                if call is None:
                    continue
                text = _text(part.get("content"))
                call.answer_bytes = len(text.encode())
                call.refused = bool(part.get("is_error"))
                call.answer_head = text[:300]
                if started and event.get("timestamp"):
                    call.ms = _ms(started, event["timestamp"])
        elif kind == "result":
            usage = event.get("usage") or {}
            run.turns = int(event.get("num_turns") or 0)
            run.input_tokens = _prompt_tokens(usage)
            run.output_tokens = int(usage.get("output_tokens") or 0)
            run.cost_usd = float(event.get("total_cost_usd") or 0)
            run.is_error = bool(event.get("is_error"))
            run.answer = str(event.get("result") or "")
    return run


def load(path: Path) -> Run:
    return parse(path.read_text(encoding="utf-8").splitlines(), path.stem)


def table(out: Path) -> str:
    """The markdown table of every `<out>/S-*.jsonl`, verdicts from `<out>/verdicts.yaml`."""
    verdicts_file = out / "verdicts.yaml"
    verdicts = (
        yaml.safe_load(verdicts_file.read_text(encoding="utf-8"))
        if verdicts_file.exists()
        else {}
    )
    baseline_file = out / "baseline.jsonl"
    baseline = load(baseline_file).start_tokens if baseline_file.exists() else 0
    rows = [
        "| scenario | pass | calls | refused | turns | tokens in | tokens out | tool defs tokens | largest answer B | USD | failure class |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for path in sorted(out.glob("S-*.jsonl")):
        run = load(path)
        verdict = (verdicts or {}).get(run.scenario) or {}
        passed = verdict.get("pass")
        mark = "unjudged" if passed is None else ("pass" if passed else "fail")
        cls = verdict.get("class") or ("" if passed else run.hint())
        if cls and cls not in CLASSES:
            raise ValueError(
                f"{verdicts_file}: {run.scenario} class {cls!r} is not one of {', '.join(CLASSES)}"
            )
        defs = run.start_tokens - baseline if baseline else run.start_tokens
        rows.append(
            f"| {run.scenario} | {mark} | {len(run.calls)} | {sum(c.refused for c in run.calls)} | {run.turns} "
            f"| {run.input_tokens} | {run.output_tokens} | {defs} | {run.largest_answer} | {run.cost_usd:.4f} | {cls} |"
        )
    return "\n".join(rows) + "\n"


def scenarios(ids: list[str] | None, source: Path = SCENARIOS) -> list[dict]:
    every = [
        s
        for s in yaml.safe_load(source.read_text(encoding="utf-8"))
        if s.get("segment") != "ui"
    ]
    if not ids:
        return every
    known = {s["id"]: s for s in every}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise SystemExit(
            f"unknown scenario id {', '.join(unknown)}; {source} lists {', '.join(known)}"
        )
    return [known[i] for i in ids]


def _claude(
    prompt: str, mcp_config: Path | None, max_turns: int, model: str | None
) -> str:
    argv = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--tools",
        "",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
    ]
    if mcp_config:
        servers = json.loads(mcp_config.read_text(encoding="utf-8"))["mcpServers"]
        argv += [
            "--mcp-config",
            str(mcp_config),
            "--allowedTools",
            *[f"mcp__{name}" for name in servers],
        ]
    if model:
        argv += ["--model", model]
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=900,
        check=False,
    )
    if not done.stdout.strip():
        raise RuntimeError(
            f"claude exited {done.returncode} with no transcript: {done.stderr.strip()[:400]}"
        )
    return scrub(done.stdout)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--mcp-config", type=Path, required=True)
    run_p.add_argument("--out", type=Path, required=True)
    run_p.add_argument(
        "--ids",
        help="comma-separated scenario ids, e.g. S-01,S-07 (default: every connector scenario)",
    )
    run_p.add_argument("--max-turns", type=int, default=12)
    run_p.add_argument("--model")
    run_p.add_argument("--scenarios", type=Path, default=SCENARIOS)
    base_p = sub.add_parser("baseline")
    base_p.add_argument("--out", type=Path, required=True)
    table_p = sub.add_parser("table")
    table_p.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    if args.command == "table":
        sys.stdout.write(table(args.out))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "baseline":
        (args.out / "baseline.jsonl").write_text(
            _claude("Answer with the single word OK.", None, 1, None), encoding="utf-8"
        )
        return 0
    for scenario in scenarios(
        args.ids.split(",") if args.ids else None, args.scenarios
    ):
        transcript = _claude(
            scenario["says"], args.mcp_config, args.max_turns, args.model
        )
        (args.out / f"{scenario['id']}.jsonl").write_text(transcript, encoding="utf-8")
        run = load(args.out / f"{scenario['id']}.jsonl")
        print(
            f"{run.scenario}: {len(run.calls)} calls, {run.turns} turns, {run.input_tokens} in, USD {run.cost_usd:.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

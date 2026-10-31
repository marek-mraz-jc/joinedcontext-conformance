"""A multi-step connector scenario through Claude Code, headless, and its verdict per step (T-6235).

A scenario file (tests/connectors/scenarios-wave/T-*.yaml) holds `id`, `title`, `project` and `steps`:
each step is one prompt (`says`, `{project}` filled in) of ONE session, so later steps build on what
earlier ones did, as a person typing into Claude Code would. `run` feeds every step on stdin as
stream-json user messages to one `claude -p`, each after the previous step's answer (only the connector's tools, as claude_code.py starts it)
and keeps the scrubbed transcript as `<out>/<id>.jsonl`; Claude Code answers each message with its
own `result` event, which is where one step ends. `verdict` reads the transcript back and prints the
verdict table, one line per step; rerunning it over the same files prints the same table.

    claude_code_run.py run --scenario FILE --mcp-config cfg.json --out DIR [--model M] [--max-turns 12]
    claude_code_run.py verdict --scenario FILE --out DIR

A step's own check: every tool of `tools` answered once without an error, every tool of `called` was
called (any answer), no tool of `deny` was called, and the step's final answer matches `answer` (a
regular expression, case-insensitive). A step that passes is `ok`; one that does not is `check` until
`<out>/verdicts.yaml` names it: `{<step number>: {verdict: ok|defect|model-error, proof: text}}`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import claude_code

VERDICTS = ("ok", "defect", "model-error")


@dataclass
class Step:
    number: int
    run: claude_code.Run
    ms: int

    def tools(self) -> list[str]:
        return [c.tool.rsplit("__", 1)[-1] for c in self.run.calls]

    def answered(self, tool: str) -> bool:
        return any(
            c.tool.rsplit("__", 1)[-1] == tool and not c.refused for c in self.run.calls
        )


def load_scenario(path: Path) -> dict:
    scenario = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("id", "project", "steps"):
        if not scenario.get(key):
            raise SystemExit(f"{path}: `{key}` is missing")
    for n, step in enumerate(scenario["steps"], 1):
        if not str(step.get("says", "")).strip():
            raise SystemExit(f"{path}: step {n} has no `says`")
        re.compile(step.get("answer", ""))
    return scenario


def messages(scenario: dict) -> str:
    """Every step as one stream-json user message, a line each."""
    return "".join(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": step["says"].replace("{project}", scenario["project"]),
                },
            }
        )
        + "\n"
        for step in scenario["steps"]
    )


def steps(lines: list[str]) -> list[Step]:
    """Split one session's transcript at each `result` event: the lines up to it are one step."""
    out: list[Step] = []
    chunk: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        chunk.append(line)
        event = json.loads(line)
        if event.get("type") == "result":
            n = len(out) + 1
            out.append(
                Step(
                    n,
                    claude_code.parse(chunk, f"step {n}"),
                    int(event.get("duration_ms") or 0),
                )
            )
            chunk = []
    return out


def check(step: Step, spec: dict) -> list[str]:
    """What the step's own check misses; empty when it holds."""
    missing = [
        f"{t} not answered" for t in spec.get("tools", []) if not step.answered(t)
    ]
    missing += [
        f"{t} not called" for t in spec.get("called", []) if t not in step.tools()
    ]
    missing += [f"{t} called" for t in spec.get("deny", []) if t in step.tools()]
    if not re.search(spec.get("answer", ""), step.run.answer, re.IGNORECASE):
        missing.append("answer does not match")
    return missing


def verdict(scenario: dict, out: Path) -> str:
    transcript = out / f"{scenario['id']}.jsonl"
    found = steps(transcript.read_text(encoding="utf-8").splitlines())
    if len(found) != len(scenario["steps"]):
        raise SystemExit(
            f"{transcript}: {len(found)} steps answered, the scenario has {len(scenario['steps'])}"
        )
    verdicts_file = out / "verdicts.yaml"
    judged = (
        yaml.safe_load(verdicts_file.read_text(encoding="utf-8")) or {}
        if verdicts_file.exists()
        else {}
    )
    rows = [
        "| step | tool calls (name x n) | ms | answer bytes | verdict | proof |",
        "|---|---|---|---|---|---|",
    ]
    for step, spec in zip(found, scenario["steps"]):
        calls = (
            ", ".join(f"{t} x {n}" for t, n in Counter(step.tools()).items()) or "none"
        )
        missing = check(step, spec)
        mine = judged.get(step.number) or {}
        mark = mine.get("verdict") or ("ok" if not missing else "check")
        if mark not in VERDICTS + ("check",):
            raise SystemExit(
                f"{verdicts_file}: step {step.number} verdict {mark!r} is not one of {VERDICTS}"
            )
        proof = mine.get("proof") or ("; ".join(missing) or "own check holds")
        refused = sum(c.refused for c in step.run.calls)
        if refused:
            calls += f" ({refused} refused)"
        tool_bytes = sum(c.answer_bytes for c in step.run.calls)
        rows.append(
            f"| {step.number} | {calls} | {step.ms} | {tool_bytes} | {mark} | {proof.replace('|', '/')} |"
        )
    return "\n".join(rows) + "\n"


def run(
    scenario: dict, mcp_config: Path, out: Path, model: str | None, max_turns: int
) -> Path:
    servers = json.loads(mcp_config.read_text(encoding="utf-8"))["mcpServers"]
    argv = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
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
        "--mcp-config",
        str(mcp_config),
        "--allowedTools",
        *[f"mcp__{name}" for name in servers],
    ]
    if model:
        argv += ["--model", model]
    # Claude Code folds user messages that wait in its queue into one turn, so each step goes in only
    # after the previous one's `result`.
    lines: list[str] = []
    with subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        for message in messages(scenario).splitlines(keepends=True):
            proc.stdin.write(message)
            proc.stdin.flush()
            for line in proc.stdout:
                lines.append(line)
                if line.strip() and json.loads(line).get("type") == "result":
                    break
            else:
                break
        proc.stdin.close()
        lines += proc.stdout.readlines()
    if not lines:
        raise RuntimeError(f"claude exited {proc.returncode} with no transcript")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{scenario['id']}.jsonl"
    path.write_text(claude_code.scrub("".join(lines)), encoding="utf-8")
    return path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--scenario", type=Path, required=True)
    run_p.add_argument("--mcp-config", type=Path, required=True)
    run_p.add_argument("--out", type=Path, required=True)
    run_p.add_argument("--model")
    run_p.add_argument("--max-turns", type=int, default=12)
    verdict_p = sub.add_parser("verdict")
    verdict_p.add_argument("--scenario", type=Path, required=True)
    verdict_p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    scenario = load_scenario(args.scenario)
    if args.command == "run":
        run(scenario, args.mcp_config, args.out, args.model, args.max_turns)
    sys.stdout.write(verdict(scenario, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Connector scenarios through Claude Code, headless, and the table of what each run cost (T-6025).

`run` starts one fresh `claude -p` session per scenario of tasks/connector-scenarios.yaml (S-01..S-50,
the segments a connector serves), with only the MCP servers of `--mcp-config` and no built-in tool,
and keeps each session's stream-json, scrubbed, as `<out>/<id>.jsonl`. `baseline` records one session
without any MCP server, so the table can tell the tool definitions' share of the first prompt. `table`
reads the saved files back, adds the written verdicts of `<out>/verdicts.yaml` and prints the markdown
table: rerunning it over the same files prints the same table.

    claude_code.py run --mcp-config cfg.json --out DIR [--ids S-01,S-07] [--max-turns 12] [--model M]
        [--scenarios portal/tests/connector_scenarios/scenarios.yaml] [--project t6025-full]
    claude_code.py baseline --out DIR
    claude_code.py table DIR
    claude_code.py wave --scenario scenarios-wave/T-6234.yaml --mcp-config cfg.json --out DIR [--model M]
    claude_code.py verdict DIR [--scenario scenarios-wave/T-6235.yaml]

`wave` runs one scenario of several steps (scenarios-wave/*.yaml: `project` and `steps`, each a
`says` a person types, `{project}` filled in) as ONE session: the first step starts it, every later
step resumes it, and each step's stream-json goes to `<out>/step-NN.jsonl`. `verdict` prints one line
per step from those files and `<out>/verdicts.yaml` (`step-01: {verdict: ok|defect|model-error,
proof: text}`). With `--scenario` it also runs each step's own check: every tool of `tools` answered
once without an error, every tool of `called` was called (a refusal counts), no tool of `deny` was
called and the step's answer matches `answer` (a regular expression, case-insensitive). A step the
check passes is `ok`, one it fails is `check` until verdicts.yaml judges it.

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
import tempfile
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
    # The person's connector works, but the test project holds nothing the scenario needs (no space,
    # Endpoint or source): the row measures the setup, not the connector.
    "fixture missing",
)

#: An answer past this many bytes is about 10k tokens of the model's context for one call.
LARGE_ANSWER = 40_000

_SECRETS = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "***"),
    # A value opening an object or array is a schema member named Authorization, not a header value.
    (
        re.compile(r'(?i)(authorization\\?"?\s*[:=]\s*\\?"?)(?![{\[])[^"\\\s,{}\[\]]+'),
        r"\1***",
    ),
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
    duration_ms: int = 0
    session_id: str = ""

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
            run.duration_ms = int(event.get("duration_ms") or 0)
            run.session_id = str(event.get("session_id") or "")
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


def prompt(scenario: dict, project: str) -> str:
    """What the person types: `says`, then the scenario's `given` facts with `{project}` filled in."""
    given = scenario.get("given")
    text = scenario["says"] if not given else f"{scenario['says']}\n\n{given}"
    return text.replace("{project}", project)


VERDICTS = ("ok", "defect", "model-error")


def check(run: Run, spec: dict) -> list[str]:
    """What a step misses of its scenario's `tools`, `called`, `deny` and `answer`; empty when it holds."""
    called = [c.tool.split("__")[-1] for c in run.calls]
    answered = {c.tool.split("__")[-1] for c in run.calls if not c.refused}
    missing = [
        f"{t} not answered" for t in spec.get("tools") or [] if t not in answered
    ]
    missing += [f"{t} not called" for t in spec.get("called") or [] if t not in called]
    missing += [f"{t} called" for t in spec.get("deny") or [] if t in called]
    if not re.search(str(spec.get("answer", "")), run.answer, re.IGNORECASE):
        missing.append("answer does not match")
    return missing


def verdict(out: Path, scenario: Path | None = None) -> str:
    """One line per `<out>/step-NN.jsonl`: calls by tool, ms, answer bytes, the verdict and proof.

    A verdict written in `<out>/verdicts.yaml` wins; else, with a scenario, the step's own check."""
    verdicts_file = out / "verdicts.yaml"
    verdicts = (
        yaml.safe_load(verdicts_file.read_text(encoding="utf-8"))
        if verdicts_file.exists()
        else {}
    ) or {}
    specs = wave(scenario)["steps"] if scenario else []
    files = sorted(out.glob("step-*.jsonl"))
    if scenario and len(files) != len(specs):
        raise SystemExit(
            f"{out}: {len(files)} step files, {scenario} has {len(specs)} steps"
        )
    rows = [
        "| step | tool calls (name x n) | ms | answer bytes | verdict | proof |",
        "|---|---|---|---|---|---|",
    ]
    for i, path in enumerate(files):
        run = load(path)
        counts: dict[str, int] = {}
        for c in run.calls:
            name = c.tool.split("__")[-1]
            counts[name] = counts.get(name, 0) + 1
        calls = ", ".join(f"{n} x {k}" for n, k in counts.items()) or "none"
        missing = check(run, specs[i]) if specs else []
        given = verdicts.get(run.scenario) or {}
        own = ("ok" if not missing else "check") if specs else "unjudged"
        mark = given.get("verdict", own)
        if mark not in VERDICTS + ("unjudged", "check"):
            raise ValueError(
                f"{verdicts_file}: {run.scenario} verdict {mark!r} is not one of {', '.join(VERDICTS)}"
            )
        default = ("; ".join(missing) or "own check holds") if specs else ""
        proof = str(given.get("proof", default)).replace("|", "\\|").replace("\n", " ")
        rows.append(
            f"| {run.scenario} | {calls} | {run.duration_ms} "
            f"| {sum(c.answer_bytes for c in run.calls)} | {mark} | {proof} |"
        )
    return "\n".join(rows) + "\n"


def wave(source: Path) -> dict:
    """A wave scenario file, every step's `says` with `{project}` filled in and its `answer` compiled."""
    doc = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    project = str(doc.get("project", ""))
    for n, step in enumerate(doc.get("steps") or [], start=1):
        said = str(step.get("says", "")).strip()
        if not said:
            raise SystemExit(f"{source}: step {n} has no `says`")
        if "{project}" in said and not project:
            raise SystemExit(
                f"{source}: step {n} names {{project}} but the file has no `project`"
            )
        step["says"] = said.replace("{project}", project)
        try:
            re.compile(str(step.get("answer", "")))
        except re.error as e:
            raise SystemExit(
                f"{source}: step {n} answer is no regular expression: {e}"
            ) from e
    if not doc.get("steps"):
        raise SystemExit(f"{source} lists no steps")
    return doc


def steps(source: Path) -> list[str]:
    """The `says` of each step of a wave scenario file, in order, `{project}` filled in."""
    return [step["says"] for step in wave(source)["steps"]]


def workdir() -> Path:
    """An empty directory for `claude -p`: run inside a clone, the model sees the repo in its prompt
    and answers with file tools it does not hold instead of the connector's (T-6249 step 1). One
    stable path, since `--resume` finds a session only under the directory it started in."""
    home = Path(tempfile.gettempdir()) / "claude-code-connector-run"
    home.mkdir(mode=0o700, exist_ok=True)
    if any(home.iterdir()):
        raise SystemExit(f"{home} is not empty; claude would read it as the project")
    return home


def _claude(
    prompt: str,
    mcp_config: Path | None,
    max_turns: int,
    model: str | None,
    resume: str | None = None,
    keep: bool = False,
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
        "--setting-sources",
        "",
        "--strict-mcp-config",
    ]
    if resume:
        argv += ["--resume", resume]
    elif not keep:
        argv.append("--no-session-persistence")
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
        cwd=workdir(),
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
    run_p.add_argument(
        "--project",
        default="t-scn",
        help="the test project `{project}` in a scenario's given text names, e.g. t6025-full",
    )
    base_p = sub.add_parser("baseline")
    base_p.add_argument("--out", type=Path, required=True)
    table_p = sub.add_parser("table")
    table_p.add_argument("out", type=Path)
    wave_p = sub.add_parser("wave")
    wave_p.add_argument("--scenario", type=Path, required=True)
    wave_p.add_argument("--mcp-config", type=Path, required=True)
    wave_p.add_argument("--out", type=Path, required=True)
    wave_p.add_argument("--max-turns", type=int, default=12)
    wave_p.add_argument("--model")
    verdict_p = sub.add_parser("verdict")
    verdict_p.add_argument("out", type=Path)
    verdict_p.add_argument(
        "--scenario", type=Path, help="the wave file whose per-step checks to run"
    )
    args = parser.parse_args(argv)

    if args.command == "table":
        sys.stdout.write(table(args.out))
        return 0
    if args.command == "verdict":
        sys.stdout.write(verdict(args.out, args.scenario))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "baseline":
        (args.out / "baseline.jsonl").write_text(
            _claude("Answer with the single word OK.", None, 1, None), encoding="utf-8"
        )
        return 0
    if args.command == "wave":
        return _wave(args)
    for scenario in scenarios(
        args.ids.split(",") if args.ids else None, args.scenarios
    ):
        transcript = _claude(
            prompt(scenario, args.project), args.mcp_config, args.max_turns, args.model
        )
        (args.out / f"{scenario['id']}.jsonl").write_text(transcript, encoding="utf-8")
        run = load(args.out / f"{scenario['id']}.jsonl")
        print(
            f"{run.scenario}: {len(run.calls)} calls, {run.turns} turns, {run.input_tokens} in, USD {run.cost_usd:.4f}"
        )
    return 0


def _wave(args: argparse.Namespace) -> int:
    session = None
    for n, says in enumerate(steps(args.scenario), start=1):
        path = args.out / f"step-{n:02d}.jsonl"
        path.write_text(
            _claude(
                says, args.mcp_config, args.max_turns, args.model, session, keep=True
            ),
            encoding="utf-8",
        )
        run = load(path)
        if not run.session_id:
            raise SystemExit(f"{path}: no session id in the result, cannot resume")
        session = run.session_id
        print(
            f"{run.scenario}: {len(run.calls)} calls, {run.turns} turns, {run.duration_ms} ms, USD {run.cost_usd:.4f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

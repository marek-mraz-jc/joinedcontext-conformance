#!/usr/bin/env python3
"""The open-data connector measured by what an agent spends on it (T-4496).

Each question of questions.json runs through `claude -p` on the subscription, with the dev MCP
hub as its only tool server and no built-in tool, and is scored against the value its NGSI-LD
read answers at the same moment. Recorded per question: tool calls, tokens (input, cache write,
cache read and output, from the stream's `result` event), and whether the answer holds the
expected value.

    agent_bench.py expected            print the expected value of each question (no model call)
    agent_bench.py run [--only ID,...] [--model sonnet] [--out DIR]
    agent_bench.py score DIR           the table again from a run's transcripts

A run writes DIR/<id>.jsonl (the stream), DIR/results.json and DIR/table.md. No API key is read
and no other model provider is called.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.json"


def load(path: Path = QUESTIONS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch(base: str, expect: dict) -> list[float]:
    """The expected values of one question, read from the Endpoint now."""
    params = {"type": expect["type"], "options": "keyValues"}
    if expect.get("q"):
        params["q"] = expect["q"]
    url = f"{base}/api/endpoint/{expect['slug']}/ngsi-ld/v1/entities"
    if expect["pick"] == "count":
        params.update(count="true", limit="0")
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=60) as r:
            return [float(r.headers["NGSILD-Results-Count"])]
    entities: list[dict] = []
    while True:
        params.update(limit="1000", offset=str(len(entities)))
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=60) as r:
            page = json.loads(r.read())
        entities += page
        if len(page) < 1000:
            break
    return pick(entities, expect)


def pick(entities: list[dict], expect: dict) -> list[float]:
    attr = expect.get("attr")
    values = [(e, e.get(attr)) for e in entities]
    numbers = [(e, float(v)) for e, v in values if isinstance(v, (int, float))]
    if not numbers:
        raise ValueError(f"no {expect['type']} carries a number in {attr}")
    kind = expect["pick"]
    if kind == "sum":
        return [sum(v for _, v in numbers)]
    if kind == "max":
        return [max(v for _, v in numbers)]
    if kind == "latest":
        newest = max(str(e.get(expect["by"], "")) for e, _ in numbers)
        return sorted({v for e, v in numbers if str(e.get(expect["by"], "")) == newest})
    raise ValueError(f"unknown pick {kind}")


_NUMBER = re.compile(r"\d(?:[\d.,   ]*\d)?")


def numbers_in(text: str) -> set[float]:
    """Every number the text could mean: `1 605`, `1,605`, `13.9`, `13,9`; a lone separator
    of three digits is read both ways."""
    out: set[float] = set()
    for token in _NUMBER.findall(text):
        token = re.sub(r"[   ]", "", token)
        readings = []
        if "," in token and "." in token:
            decimal = "," if token.rfind(",") > token.rfind(".") else "."
            thousands = "." if decimal == "," else ","
            readings.append(token.replace(thousands, "").replace(decimal, "."))
        elif "," in token or "." in token:
            sep = "," if "," in token else "."
            readings.append(token.replace(sep, ""))
            if token.count(sep) == 1:
                readings.append(token.replace(sep, "."))
        else:
            readings.append(token)
        for reading in readings:
            try:
                out.add(float(reading))
            except ValueError:
                pass
    return out


def matches(answer: str, expected: list[float], rule: dict) -> bool:
    """Whether the answer states one of the expected values, within the rule's tolerance:
    `decimals` (rounding), `relative` (a share of the value) or exact."""
    found = numbers_in(answer)
    for want in expected:
        tolerance = max(
            0.5 * 10 ** -rule["decimals"] if "decimals" in rule else 0.0,
            rule.get("relative", 0.0) * abs(want),
        )
        if any(abs(have - want) <= tolerance + 1e-9 for have in found):
            return True
    return False


def transcript(lines: list[str]) -> dict:
    """Tool calls, tokens and the final answer of one `claude -p --output-format stream-json`."""
    events = [json.loads(line) for line in lines if line.strip()]
    calls = [
        block["name"]
        for event in events
        if event.get("type") == "assistant"
        for block in event.get("message", {}).get("content", [])
        if block.get("type") == "tool_use"
    ]
    results = [event for event in events if event.get("type") == "result"]
    if not results:
        return {"calls": calls, "tokens": None, "answer": "", "error": "no result event"}
    result = results[-1]
    usage = result.get("usage") or {}
    tokens = sum(
        usage.get(key, 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    )
    return {
        "calls": calls,
        "tokens": tokens,
        "output_tokens": usage.get("output_tokens", 0),
        "turns": result.get("num_turns"),
        "answer": result.get("result", ""),
        "error": "the run ended in an error" if result.get("is_error") else None,
    }


def ask(base: str, question: str, model: str, out: Path) -> list[str]:
    config = {"mcpServers": {"opendata": {"type": "http", "url": f"{base}/api/mcp"}}}
    with tempfile.TemporaryDirectory() as cwd:
        run = subprocess.run(
            [
                "claude", "-p", question,
                "--model", model,
                "--output-format", "stream-json", "--verbose",
                "--strict-mcp-config", "--mcp-config", json.dumps(config),
                "--tools", "", "--allowedTools", "mcp__opendata",
                "--setting-sources", "", "--no-session-persistence",
            ],
            cwd=cwd, capture_output=True, text=True, timeout=900, check=False,
        )
    out.write_text(run.stdout, encoding="utf-8")
    return run.stdout.splitlines()


def table(rows: list[dict]) -> str:
    lines = ["| question | correct | calls | tokens | expected |", "|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row['id']} | {'yes' if row['correct'] else 'no'} | {len(row['calls'])} | "
            f"{row['tokens'] if row['tokens'] is not None else '-'} | {row['expected']} |"
        )
    measured = [row for row in rows if row["tokens"] is not None]
    if measured:
        lines += [
            "",
            f"correct {sum(r['correct'] for r in rows)}/{len(rows)}; "
            f"median calls {statistics.median(len(r['calls']) for r in measured)}; "
            f"median tokens {int(statistics.median(r['tokens'] for r in measured))}; "
            f"finished in two calls or fewer {sum(len(r['calls']) <= 2 for r in measured)}",
        ]
    return "\n".join(lines) + "\n"


def score(directory: Path, questions: list[dict]) -> list[dict]:
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    rows = []
    for question in questions:
        path = directory / f"{question['id']}.jsonl"
        if not path.exists():
            continue
        record = transcript(path.read_text(encoding="utf-8").splitlines())
        want = expected[question["id"]]
        record.update(
            id=question["id"],
            expected=want,
            correct=record["error"] is None and matches(record["answer"], want, question["match"]),
        )
        rows.append(record)
    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("expected")
    run = sub.add_parser("run")
    run.add_argument("--only", default="")
    run.add_argument("--model", default="sonnet")
    run.add_argument("--out", type=Path, default=None)
    again = sub.add_parser("score")
    again.add_argument("directory", type=Path)
    args = parser.parse_args(argv)

    spec = load()
    questions = spec["questions"]
    if args.command == "expected":
        for question in questions:
            print(question["id"], fetch(spec["base"], question["expect"]))
        return 0
    if args.command == "score":
        print(table(score(args.directory, questions)), end="")
        return 0

    wanted = {name for name in args.only.split(",") if name}
    questions = [q for q in questions if not wanted or q["id"] in wanted]
    out = args.out or Path(tempfile.gettempdir()) / f"agent-bench-{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    expected = {}
    for question in questions:
        # The expected value is read right before the question is asked: live data moves.
        expected[question["id"]] = fetch(spec["base"], question["expect"])
        (out / "expected.json").write_text(json.dumps(expected, indent=1), encoding="utf-8")
        ask(spec["base"], question["question"], args.model, out / f"{question['id']}.jsonl")
    rows = score(out, questions)
    (out / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "table.md").write_text(table(rows), encoding="utf-8")
    print(table(rows), end="")
    print(f"transcripts: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

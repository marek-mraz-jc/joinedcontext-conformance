#!/usr/bin/env python3
"""The CKAN assistant channel, asked what a citizen asks (T-3153, AG-05).

Each question of questions.json goes, in sk and in en, to the public chat route data.dev frames
(`POST {base}/api/v1/d/{public_id}/chat`, Origin data.dev), anonymously, as the CKAN pages do.
The answer is read from the SSE stream (answer, citations, error, done) and scored: every
`expect` regex matches the answer, no `forbid` regex does, a citation names the `cite` dataset,
and the answer is in the language asked. The spend is the sum of the `done` events' `costUsd`;
the run stops before a question once it reaches the budget.

    ckan_eval.py run [--budget 2.0] [--only ID,...] [--out DIR]
    ckan_eval.py score DIR          the table again from DIR/results.json

No key is read: the deployment pays with its own.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://assistant.dev.joinedcontext.com"
ORIGIN = "https://data.dev.joinedcontext.com"
PUBLIC_ID = "data"
TIMEOUT = 120

SK_WORDS = re.compile(r"\b(je|sú|pre|ktor\w+|dataset\w*|nájdete|alebo|podľa|údaj\w*|nie)\b|[áäčďéíĺľňóôŕšťúýž]", re.I)
EN_WORDS = re.compile(r"\b(the|is|are|for|which|you|can|of|and|dataset)\b", re.I)


def read_stream(lines) -> dict:
    """The answer text, citations, error and done event of one SSE stream."""
    out = {"answer": "", "citations": [], "error": None, "done": None}
    event = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:") and event:
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if event == "answer":
                out["answer"] += data.get("text", "") if isinstance(data, dict) else str(data)
            elif event == "citations" and isinstance(data, list):
                out["citations"] += data
            elif event == "error":
                out["error"] = data
            elif event == "done":
                out["done"] = data
    return out


def language_of(text: str) -> str:
    sk, en = len(SK_WORDS.findall(text)), len(EN_WORDS.findall(text))
    return "sk" if sk > en else "en"


def judge(q: dict, lang: str, got: dict) -> list[str]:
    """What is wrong with one answer; empty when it passes."""
    wrong = []
    if got["error"]:
        return [f"error {got['error'].get('status')}: {got['error'].get('title')}"]
    text = got["answer"]
    if not text.strip():
        return ["no answer"]
    for rx in q.get("expect", []):
        if not re.search(rx, text, re.I):
            wrong.append(f"misses /{rx}/")
    for rx in q.get("forbid", []):
        if re.search(rx, text, re.I):
            wrong.append(f"says /{rx}/")
    cite = q.get("cite")
    if cite and not any(cite in (c.get("url") or "") for c in got["citations"]):
        wrong.append(f"no citation of {cite}")
    if language_of(text) != lang:
        wrong.append(f"answers in {language_of(text)}")
    return wrong


def ask(text: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/api/v1/d/{PUBLIC_ID}/chat",
        data=json.dumps({"message": text}).encode(),
        headers={"Content-Type": "application/json", "Origin": ORIGIN, "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return read_stream(line.decode("utf-8", "replace") for line in resp)


def table(results: list[dict]) -> str:
    rows = ["| question | lang | verdict | citation | cost $ |", "|---|---|---|---|---|"]
    for r in results:
        cites = ", ".join(sorted({c.get("url", "").rsplit("/", 1)[-1] for c in r["citations"]})) or "-"
        verdict = "PASS" if not r["wrong"] else "; ".join(r["wrong"])
        rows.append(f"| {r['id']} | {r['lang']} | {verdict} | {cites} | {r['cost']:.4f} |")
    passed = sum(not r["wrong"] for r in results)
    spent = sum(r["cost"] for r in results)
    rows.append("")
    rows.append(f"PASS {passed}/{len(results)} ({100 * passed / max(len(results), 1):.0f} %); spend ${spent:.4f} (sum of done.costUsd)")
    return "\n".join(rows)


def run(args) -> int:
    questions = json.loads((HERE / "questions.json").read_text())["questions"]
    if args.only:
        keep = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in keep]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results, spent = [], 0.0
    for q in questions:
        for lang in ("sk", "en"):
            if spent >= args.budget:
                print(f"budget ${args.budget} reached at ${spent:.4f}: stopping", file=sys.stderr)
                break
            started = time.monotonic()
            try:
                got = ask(q[lang])
            except OSError as err:
                got = {"answer": "", "citations": [], "error": {"status": 0, "title": str(err)}, "done": None}
            cost = float((got["done"] or {}).get("costUsd") or 0.0)
            spent += cost
            r = {"id": q["id"], "org": q["org"], "kind": q["kind"], "lang": lang, "question": q[lang],
                 "answer": got["answer"], "citations": got["citations"], "error": got["error"],
                 "done": got["done"], "cost": cost, "seconds": round(time.monotonic() - started, 1),
                 "wrong": judge(q, lang, got)}
            results.append(r)
            print(f"{q['id']} {lang}: {'PASS' if not r['wrong'] else r['wrong']} ${cost:.4f} (total ${spent:.4f})", flush=True)
        else:
            continue
        break
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    (out / "table.md").write_text(table(results) + "\n")
    print(table(results))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--budget", type=float, default=2.0)
    r.add_argument("--only")
    r.add_argument("--out", default="ckan-eval")
    s = sub.add_parser("score")
    s.add_argument("dir")
    args = p.parse_args()
    if args.cmd == "run":
        return run(args)
    print(table(json.loads((Path(args.dir) / "results.json").read_text())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

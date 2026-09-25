#!/usr/bin/env python3
"""Measure the performance budgets on dev nightly and write a summary (T-2800, OPS-18, OPS-35).

    scripts/budgets-dev.py --out <summary.json> [--history reports/budgets-history.json]

The nightly batch runs it and hands the summary to `tasks/file-failures`. Three measures:

- endpoint: `tests/k6/dev-budgets.js`, 100 entities at JC_K6_RATE (20) requests a second through
  an endpoint and through the space's canonical surface, p95 under 300 ms. It runs only while
  every node uses less than 85 % of its memory (`kubectl top nodes`).
- pages: `e2e/budgets/pages.spec.ts` opens each main page cold as the read-only demo viewer:
  largest contentful paint under 2.5 s, layout shift at most 0.1, JavaScript under 400 KB as
  transferred (compressed).
- assistant: `jc_agent_answer_duration_seconds` read from every Portal pod through
  `kubectl port-forward`, over the answers since the last run: p50 under 3 s, p95 under 8 s.

A budget broken on one night is reported as `skip` with its value; broken two nights in a row
it is a `fail`, which files a task. A measure that could not be taken is an `error`. Every
night's values go into the history file, the trend the page and the task read.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ("spaces", "endpoints", "models", "explore", "apps", "assistant")
# name -> (ceiling, unit): a measure above its ceiling breaks the budget.
BUDGETS: dict[str, tuple[float, str]] = {
    "endpoint/p95": (300, "ms"),
    "space/p95": (300, "ms"),
    **{f"page/{p}/lcp": (2500, "ms") for p in PAGES},
    **{f"page/{p}/cls": (0.1, "") for p in PAGES},
    **{f"page/{p}/js": (400, "KB") for p in PAGES},
    "assistant/answer-p50": (3, "s"),
    "assistant/answer-p95": (8, "s"),
}
MEMORY_CEILING = 85
ANSWERS = "jc_agent_answer_duration_seconds_bucket"
KEEP_RUNS = 60
BUCKET = re.compile(r'^' + ANSWERS + r'\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.eE+-]+)')


def result(key: str, verdict: str, title: str, detail: str = "", evidence: str = "") -> dict:
    return {"key": key, "verdict": verdict, "title": title, "detail": detail, "evidence": evidence}


# --- parsing ----------------------------------------------------------------------------------


def memory_percent(top: str) -> int:
    """The highest memory use of `kubectl top nodes --no-headers`: NAME CPU CPU% MEMORY MEMORY%."""
    values = [int(line.split()[4].rstrip("%")) for line in top.splitlines() if len(line.split()) >= 5]
    if not values:
        raise ValueError("kubectl top nodes printed no node")
    return max(values)


def k6_measures(summary: dict) -> tuple[dict[str, float], dict[str, str]]:
    """p95 per surface from the k6 summary, and why a surface was not measured."""
    metrics = summary.get("metrics", {})
    measured, missing = {}, {}
    for surface in ("endpoint", "space"):
        refused = metrics.get(f"budget_{surface}_rate_limited", {}).get("values", {}).get("count", 0)
        trend = metrics.get(f"budget_{surface}_read_ms", {}).get("values", {})
        if refused:
            missing[f"{surface}/p95"] = (f"{refused:.0f} requests answered 429: the surface's rate limit is below the "
                                         "offered load, so dev needs a test endpoint that allows it")
        elif "p(95)" not in trend or not trend.get("count"):
            missing[f"{surface}/p95"] = "k6 recorded no read"
        else:
            measured[f"{surface}/p95"] = trend["p(95)"]
    checks = metrics.get("checks", {}).get("values", {})
    if checks.get("fails"):
        for key in list(measured):
            missing[key] = f"{checks['fails']:.0f} reads did not answer 200 with entities"
            del measured[key]
    return measured, missing


def page_measures(pages: dict) -> tuple[dict[str, float], dict[str, str]]:
    measured, missing = {}, {}
    found = {entry["page"]: entry for entry in pages.get("pages", [])}
    for page in PAGES:
        entry = found.get(page)
        if entry is None:
            for part in ("lcp", "cls", "js"):
                missing[f"page/{page}/{part}"] = "the page was not measured"
            continue
        if entry.get("lcpMs") is None:
            missing[f"page/{page}/lcp"] = "the browser reported no largest contentful paint"
        else:
            measured[f"page/{page}/lcp"] = entry["lcpMs"]
        measured[f"page/{page}/cls"] = entry["cls"]
        measured[f"page/{page}/js"] = entry["jsBytes"] / 1024
    return measured, missing


def answer_buckets(texts: list[str]) -> dict[str, float]:
    """The answer histogram's cumulative buckets, summed over every pod and label set."""
    buckets: dict[str, float] = {}
    for text in texts:
        for line in text.splitlines():
            found = BUCKET.match(line)
            if not found:
                continue
            le = re.search(r'le="([^"]+)"', found["labels"])
            if le:
                buckets[le[1]] = buckets.get(le[1], 0) + float(found["value"])
    return buckets


def since(now: dict[str, float], before: dict[str, float] | None) -> dict[str, float]:
    """The answers since the last run; a Portal restart zeroed the counters, so all of them then."""
    if not before or any(now.get(le, 0) < count for le, count in before.items()):
        return dict(now)
    return {le: count - before.get(le, 0) for le, count in now.items()}


def quantile(q: float, buckets: dict[str, float]) -> float | None:
    """Prometheus' histogram_quantile over cumulative buckets; None without an observation."""
    bounds = sorted((math.inf if le == "+Inf" else float(le), count) for le, count in buckets.items())
    if not bounds or bounds[-1][1] <= 0:
        return None
    rank = q * bounds[-1][1]
    lower, below = 0.0, 0.0
    for bound, count in bounds:
        if count >= rank:
            if math.isinf(bound):
                return lower
            return lower + (bound - lower) * ((rank - below) / (count - below) if count > below else 0)
        lower, below = bound, count
    return lower


# --- the verdict ------------------------------------------------------------------------------


def verdicts(measured: dict[str, float], missing: dict[str, str], broken_before: set[str],
             quiet: dict[str, str]) -> list[dict]:
    """One result per budget: `quiet` measures had nothing to measure (skip, not a failure)."""
    results = []
    for key, (ceiling, unit) in BUDGETS.items():
        shown = f"{ceiling:g} {unit}".strip()
        if key in quiet:
            results.append(result(f"budget/{key}", "skip", f"{key}: not measured tonight", quiet[key]))
        elif key in missing:
            results.append(result(f"budget/{key}", "error", f"{key}: not measured on dev", missing[key]))
        elif measured[key] <= ceiling:
            results.append(result(f"budget/{key}", "pass", f"{key} {measured[key]:.3g} {unit} is within {shown}"))
        elif key in broken_before:
            results.append(result(f"budget/{key}", "fail", f"{key} {measured[key]:.3g} {unit} is over {shown} two nights in a row",
                                  "the history file holds the nights before"))
        else:
            results.append(result(f"budget/{key}", "skip", f"{key} {measured[key]:.3g} {unit} is over {shown} tonight",
                                  "a second night over the budget files a task"))
    return results


def broken(measured: dict[str, float]) -> list[str]:
    return sorted(key for key, value in measured.items() if value > BUDGETS[key][0])


# --- the run ----------------------------------------------------------------------------------


def run(command: list[str], env: dict, timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout} s"


def profile_env() -> dict:
    """The `budgets` profile of tests/dev.env.sh, as the environment the tools run with."""
    code, out = run(["bash", "-c", '. tests/dev.env.sh budgets >&2 && env -0'], dict(os.environ), 120)
    if code != 0:
        raise RuntimeError(f"tests/dev.env.sh budgets failed: {out[-300:]}")
    return dict(item.split("=", 1) for item in out.split("\0") if "=" in item)


def measure_endpoints(env: dict, reports: Path) -> tuple[dict, dict]:
    keys = ("endpoint/p95", "space/p95")
    code, top = run(["kubectl", "top", "nodes", "--no-headers"], env, 60)
    if code != 0:
        return {}, {k: f"kubectl top nodes failed, so no load was offered: {top[-200:]}" for k in keys}
    used = memory_percent(top)
    if used >= MEMORY_CEILING:
        return {}, {k: f"a node uses {used} % of its memory (ceiling {MEMORY_CEILING} %), so no load was offered" for k in keys}
    missing_env = [name for name in ("ENDPOINT_URL", "SPACE_URL", "BUDGET_TYPE") if not env.get(name)]
    if missing_env:
        return {}, {k: f"not set on dev: {', '.join(missing_env)}" for k in keys}
    summary = reports / "dev-budgets.json"
    summary.unlink(missing_ok=True)
    code, out = run(["k6", "run", "--quiet", "tests/k6/dev-budgets.js"], dict(env, JC_REPORTS_DIR=str(reports)), 900)
    if not summary.is_file():
        return {}, {k: f"k6 wrote no summary (exit {code}): {out[-200:]}" for k in keys}
    return k6_measures(json.loads(summary.read_text()))


def measure_pages(env: dict, reports: Path) -> tuple[dict, dict]:
    out_file = reports / "pages.json"
    out_file.unlink(missing_ok=True)
    code, out = run(["npx", "playwright", "test", "e2e/budgets", "--config", "e2e/playwright.config.ts"],
                    dict(env, JC_BUDGETS_OUT=str(out_file), JC_REPORTS_DIR=str(reports / "playwright")), 900)
    if not out_file.is_file():
        reason = f"the page run wrote no measures (exit {code}): {' '.join(out.split())[-200:]}"
        return {}, {f"page/{p}/{part}": reason for p in PAGES for part in ("lcp", "cls", "js")}
    return page_measures(json.loads(out_file.read_text()))


def portal_metrics(env: dict) -> list[str]:
    """Each Portal pod's /metrics, through a port-forward: the edge refuses /metrics by design."""
    namespace = env.get("JC_DEV_NS", "dev")
    code, names = run(["kubectl", "get", "pods", "-n", namespace, "-l", "app.kubernetes.io/name=portal-portal",
                       "--field-selector=status.phase=Running", "-o", "name"], env, 60)
    if code != 0 or not names.split():
        raise RuntimeError(f"no running Portal pod in {namespace}: {names[-200:]}")
    texts = []
    for name in names.split():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        forward = subprocess.Popen(["kubectl", "port-forward", "-n", namespace, name, f"{port}:8080"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(20):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=10) as answer:
                        texts.append(answer.read().decode())
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError(f"{name}: /metrics did not answer through the port-forward")
        finally:
            forward.terminate()
            forward.wait(timeout=10)
    return texts


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path, help="where the summary JSON goes")
    parser.add_argument("--history", type=Path, default=ROOT / "reports" / "budgets-history.json")
    args = parser.parse_args(argv)
    if not os.environ.get("KUBECONFIG"):
        parser.error("KUBECONFIG must point at the dev cluster")
    reports = ROOT / "reports" / "budgets"
    reports.mkdir(parents=True, exist_ok=True)
    history = json.loads(args.history.read_text()) if args.history.is_file() else {"runs": []}
    last = history["runs"][-1] if history["runs"] else {}

    measured: dict[str, float] = {}
    missing: dict[str, str] = {}
    quiet: dict[str, str] = {}
    buckets: dict[str, float] = {}
    try:
        env = profile_env()
    except RuntimeError as error:
        env = None
        missing = {key: str(error) for key in BUDGETS}
    if env is not None:
        for measure in (measure_endpoints, measure_pages):
            found, absent = measure(env, reports)
            measured |= found
            missing |= absent
        try:
            buckets = answer_buckets(portal_metrics(env))
            answers = since(buckets, last.get("answerBuckets"))
            for key, q in (("assistant/answer-p50", 0.5), ("assistant/answer-p95", 0.95)):
                value = quantile(q, answers)
                if value is None:
                    quiet[key] = "no assistant answer since the last run"
                else:
                    measured[key] = value
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            missing |= {"assistant/answer-p50": str(error), "assistant/answer-p95": str(error)}

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    results = verdicts(measured, missing, set(last.get("broken", [])), quiet)
    history["runs"] = (history["runs"] + [{"run": stamp, "values": measured, "broken": broken(measured),
                                           "answerBuckets": buckets or last.get("answerBuckets", {})}])[-KEEP_RUNS:]
    args.history.parent.mkdir(parents=True, exist_ok=True)
    args.history.write_text(json.dumps(history, indent=1) + "\n")
    summary = {"check": "budgets", "repo": "joinedcontext-conformance", "run": stamp,
               "requirements": ["OPS-18", "OPS-35"], "results": results}
    args.out.write_text(json.dumps(summary, indent=2) + "\n")
    bad = [r for r in results if r["verdict"] in ("fail", "error")]
    for r in results:
        print(f"{r['verdict']:5} {r['title']}")
    print(f"{len(results)} budgets, {len(bad)} failing; summary in {args.out}, history in {args.history}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

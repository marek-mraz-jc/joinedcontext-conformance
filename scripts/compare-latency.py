#!/usr/bin/env python3
"""Nightly benchmark latency regression gate (TS-22, OPS-18, T-0090).

Compares k6 latency results against rolling 7-day history and gates regressions over 5%.

    compare-latency.py summary.json benchmarks/latency-history.json [--commit <hash>]
    compare-latency.py --selftest
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import tempfile
from pathlib import Path


def read_summary(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{path}: unreadable JSON: {exc}") from None

    metrics = data.get("metrics", {})
    if "ops18_gateway_read_ms" not in metrics:
        raise ValueError(f"{path}: missing metric 'ops18_gateway_read_ms'")

    values = metrics["ops18_gateway_read_ms"].get("values", {})
    if "p(95)" not in values or "p(99)" not in values:
        raise ValueError(f"{path}: 'ops18_gateway_read_ms' missing p(95) or p(99)")

    return {
        "p95": float(values["p(95)"]),
        "p99": float(values["p(99)"]),
    }


def parse_timestamp(ts: str) -> datetime.datetime:
    cleaned = ts.strip().replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def rolling(history: dict, days: int = 7, now: datetime.datetime | None = None) -> dict:
    runs = history.get("runs", [])
    current_time = now or datetime.datetime.now(datetime.timezone.utc)
    valid_runs: list[dict] = []

    for r in runs:
        try:
            run_dt = parse_timestamp(r["at"])
            if current_time - run_dt <= datetime.timedelta(days=days):
                valid_runs.append(r)
        except (KeyError, ValueError):
            continue

    if len(valid_runs) < 3:
        return {}

    return {
        "p95": sum(r["p95"] for r in valid_runs) / len(valid_runs),
        "p99": sum(r["p99"] for r in valid_runs) / len(valid_runs),
        "count": len(valid_runs),
    }


def regressions(current: dict, baseline: dict, *, tolerance: float = 0.05) -> list[str]:
    if not baseline or "p95" not in baseline or "p99" not in baseline:
        return []

    problems: list[str] = []
    for metric in ("p95", "p99"):
        base_val = baseline[metric]
        curr_val = current[metric]
        if base_val <= 0:
            continue
        rise = (curr_val - base_val) / base_val
        if rise > tolerance:
            pct = rise * 100.0
            problems.append(
                f"{metric} regression: rose by {pct:.1f}% (baseline {base_val:.2f} ms, current {curr_val:.2f} ms, tolerance {tolerance * 100:.0f}%)"
            )
    return problems


def append(history: dict, entry: dict) -> dict:
    runs = list(history.get("runs", []))
    runs.append(entry)
    runs.sort(key=lambda r: r.get("at", ""))
    history["runs"] = runs[-60:]
    return history


def selftest() -> int:
    ref_time = datetime.datetime(2026, 4, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    flat_history = {
        "runs": [
            {"at": "2026-04-08T12:00:00Z", "p95": 10.0, "p99": 20.0, "commit": "c1"},
            {"at": "2026-04-09T12:00:00Z", "p95": 10.0, "p99": 20.0, "commit": "c2"},
            {"at": "2026-04-10T10:00:00Z", "p95": 10.0, "p99": 20.0, "commit": "c3"},
        ]
    }
    baseline = rolling(flat_history, days=7, now=ref_time)
    assert baseline.get("p95") == 10.0 and baseline.get("p99") == 20.0

    # 1. Flat history plus a 10% slower run (fails, naming p95)
    slower_10 = {"p95": 11.0, "p99": 20.0}
    reg_10 = regressions(slower_10, baseline)
    assert reg_10 and any("p95" in p for p in reg_10), f"expected p95 regression, got {reg_10}"
    print("flat history + 10% slower run flags regression naming p95")

    # 2. A 4% slower run (passes)
    slower_4 = {"p95": 10.4, "p99": 20.8}
    reg_4 = regressions(slower_4, baseline)
    assert not reg_4, f"expected 4% slower run to pass, got {reg_4}"
    print("4% slower run passes within tolerance")

    # 3. A history of two runs (no baseline, exit 0)
    history_2 = {"runs": flat_history["runs"][:2]}
    baseline_2 = rolling(history_2, days=7, now=ref_time)
    assert not baseline_2, f"expected no baseline for 2 runs, got {baseline_2}"
    assert regressions(slower_10, baseline_2) == []
    print("history with two runs reports no baseline yet and passes")

    # 4. A summary missing the metric (error)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as tmp:
        tmp.write(json.dumps({"metrics": {}}))
        tmp.flush()
        try:
            read_summary(Path(tmp.name))
            raise AssertionError("expected missing metric to raise ValueError")
        except ValueError as exc:
            assert "ops18_gateway_read_ms" in str(exc)
    print("summary missing ops18_gateway_read_ms raises error")

    # 5. A history with entries older than seven days that must be ignored
    history_with_old = {
        "runs": [
            {"at": "2026-04-01T10:00:00Z", "p95": 100.0, "p99": 200.0, "commit": "old1"},
            {"at": "2026-04-02T10:00:00Z", "p95": 100.0, "p99": 200.0, "commit": "old2"},
            {"at": "2026-04-08T10:00:00Z", "p95": 10.0, "p99": 20.0, "commit": "c1"},
            {"at": "2026-04-09T10:00:00Z", "p95": 10.0, "p99": 20.0, "commit": "c2"},
            {"at": "2026-04-10T10:00:00Z", "p95": 10.0, "p99": 20.0, "commit": "c3"},
        ]
    }
    baseline_filtered = rolling(history_with_old, days=7, now=ref_time)
    assert abs(baseline_filtered["p95"] - 10.0) < 1e-6, f"old entries not ignored: {baseline_filtered}"
    print("history entries older than seven days are ignored")

    # 6. An improvement of 20% (passes and is recorded)
    improved_20 = {"p95": 8.0, "p99": 16.0}
    reg_improved = regressions(improved_20, baseline)
    assert not reg_improved, f"expected improvement to pass, got {reg_improved}"
    entry = {"at": "2026-04-10T11:00:00Z", "p95": 8.0, "p99": 16.0, "commit": "c4"}
    updated = append({"runs": list(flat_history["runs"])}, entry)
    assert any(r["p95"] == 8.0 for r in updated["runs"]), "improvement was not recorded"
    print("20% improvement passes and is recorded")

    print("ok: latency regression gate flags regressions, respects tolerance, and maintains rolling baseline")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("summary", nargs="?", type=Path, help="k6 summary.json output")
    parser.add_argument("history", nargs="?", type=Path, help="benchmarks/latency-history.json file")
    parser.add_argument("--commit", default="", help="commit hash for current benchmark run")
    parser.add_argument("--tolerance", type=float, default=0.05, help="regression tolerance threshold (default: 0.05)")
    parser.add_argument("--selftest", action="store_true", help="run internal test suite")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if not args.summary or not args.history:
        parser.error("both summary and history files are required (or use --selftest)")

    current = read_summary(args.summary)
    history: dict = {"runs": []}
    if args.history.exists():
        try:
            history = json.loads(args.history.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"warning: unreadable history file {args.history}: {exc}", file=sys.stderr)

    baseline = rolling(history)
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "p95": current["p95"],
        "p99": current["p99"],
        "commit": args.commit,
    }

    if not baseline:
        print(f"no baseline yet (history holds fewer than 3 runs within 7 days); recording run (p95={current['p95']:.2f}ms, p99={current['p99']:.2f}ms)")
        append(history, entry)
        args.history.parent.mkdir(parents=True, exist_ok=True)
        args.history.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
        return 0

    problems = regressions(current, baseline, tolerance=args.tolerance)
    if problems:
        for p in problems:
            print(f"FAIL {p}", file=sys.stderr)
        return 1

    print(f"ok: latency within budget (p95={current['p95']:.2f}ms vs baseline {baseline['p95']:.2f}ms, p99={current['p99']:.2f}ms vs baseline {baseline['p99']:.2f}ms)")
    append(history, entry)
    args.history.parent.mkdir(parents=True, exist_ok=True)
    args.history.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

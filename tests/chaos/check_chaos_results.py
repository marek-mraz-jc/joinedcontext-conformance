#!/usr/bin/env python3
"""Verdict of a chaos run (T-0088, CC-55, OPS-18).

The k6 CSV output carries one row per request with its timestamp and whether it failed; the
disruption log carries one line per injected fault. Together they answer the only three questions
CC-55 asks: did the surface keep serving, how long was it down, and did it come back on its own.

A chaos run that ends with exit code 0 proves nothing by itself — k6 exits 0 on a run whose every
request failed as long as no threshold was declared. This script is the verdict.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# CC-55 budget: the surface stays available across a single-replica disruption.
MAX_ERROR_RATE = 0.001          # 0.1 %
MAX_OUTAGE_SECONDS = 30.0       # recovery window after a disruption
MIN_REQUESTS = 100              # a run this small measured nothing


@dataclass(frozen=True)
class Sample:
    at: float
    failed: bool


@dataclass(frozen=True)
class Disruption:
    at: float
    what: str


def read_samples(path: Path) -> list[Sample]:
    """k6 `--out csv=` rows. Only http_req_failed carries the verdict per request."""
    samples: list[Sample] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("metric_name") != "http_req_failed":
                continue
            try:
                at = float(row["timestamp"])
                value = float(row["metric_value"])
            except (KeyError, TypeError, ValueError) as bad_row:
                raise ValueError(f"{path}: unreadable http_req_failed row {row!r}: {bad_row}") from None
            samples.append(Sample(at=at, failed=value != 0.0))
    return sorted(samples, key=lambda s: s.at)


def read_disruptions(path: Path) -> list[Disruption]:
    """One `<unix seconds> <what>` line per injected fault, written by run.sh."""
    disruptions: list[Disruption] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        at, _, what = line.partition(" ")
        try:
            disruptions.append(Disruption(at=float(at), what=what.strip() or "unnamed disruption"))
        except ValueError:
            raise ValueError(f"{path}:{number}: expected '<unix seconds> <what>', got {line!r}") from None
    return sorted(disruptions, key=lambda d: d.at)


def longest_outage(samples: list[Sample]) -> tuple[float, float]:
    """(seconds, started_at) of the longest run of consecutive failures.

    Measured from the first failure to the first success after it, because that is what a client
    experiences: the gap between the last answer and the next one.
    """
    longest = 0.0
    started_at = 0.0
    outage_start: float | None = None
    for sample in samples:
        if sample.failed:
            if outage_start is None:
                outage_start = sample.at
            continue
        if outage_start is not None:
            length = sample.at - outage_start
            if length > longest:
                longest, started_at = length, outage_start
            outage_start = None
    if outage_start is not None and samples:
        length = samples[-1].at - outage_start
        if length > longest:
            longest, started_at = length, outage_start
    return longest, started_at


def recovery_after(samples: list[Sample], disruption: Disruption) -> float | None:
    """Seconds from a disruption to the first successful request after it, None if none came."""
    for sample in samples:
        if sample.at >= disruption.at and not sample.failed:
            return sample.at - disruption.at
    return None


def verdict(samples: list[Sample], disruptions: list[Disruption]) -> list[str]:
    """Every CC-55 violation, as readable lines. Empty means the run holds."""
    problems: list[str] = []
    if len(samples) < MIN_REQUESTS:
        problems.append(
            f"the run carried {len(samples)} requests, fewer than the {MIN_REQUESTS} a verdict needs: "
            "a chaos run that barely ran cannot show availability"
        )
    if not disruptions:
        problems.append("no disruption was injected: a chaos run without a fault proves nothing")
    if not samples:
        return problems

    failed = sum(1 for sample in samples if sample.failed)
    rate = failed / len(samples)
    if rate > MAX_ERROR_RATE:
        problems.append(
            f"error rate {rate:.4%} over {len(samples)} requests exceeds the CC-55 budget of {MAX_ERROR_RATE:.2%}"
        )

    outage, outage_start = longest_outage(samples)
    if outage > MAX_OUTAGE_SECONDS:
        problems.append(
            f"the surface was unavailable for {outage:.1f}s starting at {outage_start:.0f}, "
            f"longer than the {MAX_OUTAGE_SECONDS:.0f}s recovery budget"
        )

    for disruption in disruptions:
        recovery = recovery_after(samples, disruption)
        if recovery is None:
            problems.append(f"no request succeeded after '{disruption.what}': the surface never came back")
        elif recovery > MAX_OUTAGE_SECONDS:
            problems.append(
                f"'{disruption.what}' took {recovery:.1f}s to answer again, over the "
                f"{MAX_OUTAGE_SECONDS:.0f}s budget"
            )
    return problems


def report(samples: list[Sample], disruptions: list[Disruption]) -> str:
    failed = sum(1 for sample in samples if sample.failed)
    outage, _ = longest_outage(samples)
    lines = [
        f"requests: {len(samples)}, failed: {failed}",
        f"error rate: {(failed / len(samples) if samples else 0):.4%} (budget {MAX_ERROR_RATE:.2%})",
        f"longest outage: {outage:.1f}s (budget {MAX_OUTAGE_SECONDS:.0f}s)",
    ]
    for disruption in disruptions:
        recovery = recovery_after(samples, disruption)
        lines.append(
            f"{disruption.what}: recovered in {recovery:.1f}s" if recovery is not None
            else f"{disruption.what}: never recovered"
        )
    return "\n".join(lines)


def selftest() -> int:
    """Synthetic runs: the verdict has to accept a survivable disruption and reject the rest."""
    def run(pattern: list[tuple[float, bool]], faults: list[tuple[float, str]]) -> list[str]:
        return verdict(
            [Sample(at=at, failed=failed) for at, failed in pattern],
            [Disruption(at=at, what=what) for at, what in faults],
        )

    # a pod dies at t=100, one in-flight request is lost, the next already lands on the survivor
    survivable = [(float(t), False) for t in range(0, 100)] + [(100.5, True)] + [
        (float(t), False) for t in range(101, 2000)
    ]
    assert not run(survivable, [(100.0, "delete one context-gateway pod")]), run(
        survivable, [(100.0, "delete one context-gateway pod")]
    )

    # the same run without a fault is not a chaos run
    assert any("without a fault" in problem for problem in run(survivable, []))

    # 40 seconds of nothing but errors: over the recovery budget
    outage = (
        [(float(t), False) for t in range(0, 100)]
        + [(float(t), True) for t in range(100, 141)]
        + [(float(t), False) for t in range(141, 200)]
    )
    problems = run(outage, [(100.0, "delete the only APISIX pod")])
    assert any("unavailable for" in problem for problem in problems), problems
    assert any("to answer again" in problem for problem in problems), problems

    # scattered failures below the outage budget but above the error rate
    scattered = [(float(t), t % 20 == 0) for t in range(0, 400)]
    problems = run(scattered, [(100.0, "CNPG switchover")])
    assert any("error rate" in problem for problem in problems), problems
    assert not any("unavailable for" in problem for problem in problems), problems

    # a surface that never answers again
    dead = [(float(t), False) for t in range(0, 100)] + [(float(t), True) for t in range(100, 200)]
    assert any("never came back" in problem for problem in run(dead, [(100.0, "delete both replicas")]))

    # a run too small to mean anything
    assert any("fewer than" in problem for problem in run([(1.0, False), (2.0, False)], [(1.5, "x")]))

    print("ok: the chaos verdict accepts a survivable disruption and rejects an outage, a slow "
          "recovery, a raised error rate, a dead surface, a tiny run and a run with no fault")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", help="k6 --out csv= output of the run")
    parser.add_argument("disruptions", nargs="?", help="the disruption log written by run.sh")
    parser.add_argument("--json", help="write the machine-readable verdict here")
    parser.add_argument("--selftest", action="store_true", help="check the verdict logic itself")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if not args.csv or not args.disruptions:
        parser.error("both the k6 CSV and the disruption log are required")

    samples = read_samples(Path(args.csv))
    disruptions = read_disruptions(Path(args.disruptions))
    problems = verdict(samples, disruptions)

    print(report(samples, disruptions))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"problems": problems, "requests": len(samples)}, indent=2), encoding="utf-8"
        )
    if problems:
        print("\nCC-55 violations:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("\nCC-55 holds: the surface stayed available across every injected disruption")
    return 0


if __name__ == "__main__":
    sys.exit(main())

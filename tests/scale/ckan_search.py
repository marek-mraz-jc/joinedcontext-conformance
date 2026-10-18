"""Load the generated catalogue into a throwaway CKAN and time `package_search` (T-4544).

    python3 tests/scale/ckan_search.py load    --url http://127.0.0.1:42125 --token-file F
    python3 tests/scale/ckan_search.py measure --url http://127.0.0.1:42125

`load` creates the publishers' organizations and the packages (idempotent: a name already
there is skipped). `measure` asks 200 questions, a third sk with diacritics, a third sk folded
to ASCII, a third en (T-4497's mix), each with two facets and a facet filter on the type tag,
and prints p50/p95 and how often a dataset about the asked (topic, place, year) is in the first
three. Every number it prints is measured here. The token is read from a file and never printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))

import catalogue  # noqa: E402

TIMEOUT = 60


def post(headers: dict[str, str], url: str, action: str, body: dict) -> dict:
    """One Action API call on a connection of its own: uWSGI's plain `--http` router closes
    each connection after one answer, which a pooled session would reuse."""
    r = requests.post(f"{url}/api/3/action/{action}", json=body, headers=headers, timeout=TIMEOUT)
    answer = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not answer.get("success"):
        raise RuntimeError(f"{action}: HTTP {r.status_code} {json.dumps(answer.get('error'))[:300]}")
    return answer["result"]


def exists(error: Exception) -> bool:
    return "already" in str(error) or "URL is already in use" in str(error)


def load(url: str, token: str, seed: int, n: int, workers: int) -> None:
    datasets = list(catalogue.catalogue(seed, n))
    session = {"Authorization": token}
    for org in catalogue.organizations(datasets):
        try:
            post(session, url, "organization_create", org)
        except RuntimeError as e:
            if not exists(e):
                raise

    def create(d: catalogue.Dataset) -> None:
        try:
            post(session, url, "package_create", catalogue.package(d))
        except RuntimeError as e:
            if not exists(e):
                raise

    started = time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(create, datasets))
    took = time.monotonic() - started
    count = post(session, url, "package_search", {"rows": 0})["count"]
    print(f"loaded {count} packages in {took:.1f}s")


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def questions(seed: int, n: int, count: int = 200) -> list[tuple[str, str, set[str]]]:
    """(q, type tag, names of every dataset about the same topic, place and year)."""
    datasets = list(catalogue.catalogue(seed, n))
    about: dict[tuple[str, str, int], set[str]] = {}
    for d in datasets:
        about.setdefault((d.entity_type, d.place, d.year), set()).add(catalogue.package(d)["name"])
    step = max(len(datasets) // count, 1)
    out = []
    for k, d in enumerate(datasets[::step][:count]):
        sk = d.title["sk"]
        q = (sk, fold(sk), d.title["en"])[k % 3]
        out.append((q, d.entity_type, about[(d.entity_type, d.place, d.year)]))
    return out


def measure(url: str, seed: int, n: int) -> dict:
    session: dict[str, str] = {}
    took, found = [], 0
    asked = questions(seed, n)
    for q, entity_type, targets in asked:
        body = {
            "q": q,
            "fq": f'tags:"{entity_type}"',
            "facet": "true",
            "facet.field": ["organization", "tags"],
            "rows": 10,
        }
        started = time.perf_counter()
        result = post(session, url, "package_search", body)
        took.append((time.perf_counter() - started) * 1000)
        if any(p["name"] in targets for p in result["results"][:3]):
            found += 1
    took.sort()
    p95 = took[min(int(len(took) * 0.95), len(took) - 1)]
    return {
        "queries": len(took),
        "p50_ms": round(statistics.median(took), 1),
        "p95_ms": round(p95, 1),
        "top3": f"{found}/{len(asked)}",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("load", "measure"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file")
    parser.add_argument("--seed", type=int, default=4497)
    parser.add_argument("-n", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    url = args.url.rstrip("/")
    if args.step == "load":
        if not args.token_file:
            parser.error("load needs --token-file")
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
        load(url, token, args.seed, args.n, args.workers)
    else:
        print(json.dumps(measure(url, args.seed, args.n)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

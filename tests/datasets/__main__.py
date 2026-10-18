"""python3 -m tests.datasets <ckan-dataset-name>... | --all [--json] [--ws-seconds 60]

Validates each published dataset end to end, read only (see validate.py), prints one line per
check and exits 1 on any FAIL. BASE_URL (default https://api.dev.joinedcontext.com, the data host) is the
installation, CKAN_URL (default https://data.<BASE_URL host>) its catalogue, SEED_DIR the
deployment seed the Pipelines' schedules and DataSources are read from, JC_GATEWAY_TOKEN an
optional ServiceAccount token for a non-public Endpoint (never printed).
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from .validate import SEED_DIR, Http, validate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tests.datasets", description=__doc__.splitlines()[2])
    parser.add_argument("datasets", nargs="*", help="CKAN dataset names")
    parser.add_argument("--all", action="store_true", help="every dataset of the catalogue")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON instead of the table")
    parser.add_argument("--ws-seconds", type=float, default=60, help="how long to listen for a notification")
    parser.add_argument("--no-whole-exports", action="store_true",
                        help="skip the whole-dataset file.* downloads (T-4107: one took dev's gateway down)")
    parser.add_argument("--parallel", type=int, default=2, help="datasets checked at once (dev is one node)")
    args = parser.parse_args(argv)
    base_url = os.environ.get("BASE_URL", "https://api.dev.joinedcontext.com").rstrip("/")
    parts = urlsplit(base_url)
    ckan_url = os.environ.get("CKAN_URL", f"{parts.scheme}://data.{(parts.hostname or '').removeprefix('api.')}").rstrip("/")
    http = Http(os.environ.get("JC_GATEWAY_TOKEN") or None, parts.hostname)

    names = list(args.datasets)
    if args.all:
        r = http.fetch(f"{ckan_url}/api/3/action/package_search?rows=1000&fl=name", "application/json")
        if r.status != 200:
            print(f"FAIL package_search HTTP {r.status}", file=sys.stderr)
            return 1
        names += [p["name"] for p in json.loads(r.body)["result"]["results"] if p["name"] not in names]
    if not names:
        parser.error("name a dataset or pass --all")

    start = time.monotonic()
    with ThreadPoolExecutor(max(1, args.parallel)) as pool:
        reports = list(pool.map(lambda n: validate(http, n, base_url, ckan_url, Path(SEED_DIR), args.ws_seconds,
                                                   whole_exports=not args.no_whole_exports), names))
    checks = [c for report in reports for c in report]
    failed = sum(c.verdict == "FAIL" for c in checks)
    if args.json:
        json.dump([asdict(c) for c in checks], sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        for name, report in zip(names, reports):
            print(f"\n## {name}: {sum(c.verdict == 'FAIL' for c in report)} FAIL of {len(report)} checks")
            for c in report:
                print(f"{c.verdict:4}  {c.check:44}  {c.measured}{'  | ' + c.detail if c.detail else ''}")
        print(f"\n{len(names)} datasets, {len(checks)} checks, {failed} FAIL, {time.monotonic() - start:.0f}s")
    return 1 if failed else 0


sys.exit(main())

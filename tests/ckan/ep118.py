"""Does every dataset of the catalogue list what EP-118 makes mandatory, and does each link answer (T-4324)?

The owner's rule of 2026-10-16 (docs Architecture/21-open-data-catalogue.md §9): every dataset
lists NGSI-LD, MCP, WebSocket, CSV, JSON and its data model in seven formalisms (JSON Schema,
JSON-LD, LinkML, Markdown, OWL, RDF, SHACL); GeoJSON and OGC API - Features when its rows carry a
location; never ZIP or Esri REST; and every listed link answers 200 to an anonymous read.

Each link is read once, small: an Endpoint's data link with `limit=1` (`$top=1` for OData), a
model artefact whole, the WebSocket by `x-subscribe` and MCP by `initialize`. Whether the rows
carry a location is read off the NGSI-LD link's first entity, so a located dataset that lists no
GeoJSON is caught. A link outside the catalogue's own resources, such as a source's download, is
read the same way.

    python3 tests/ckan/ep118.py --ckan https://data.dev.joinedcontext.com [--pace 0.3] [organization ...]

prints one Markdown row per dataset (each mandatory distribution, what is banned, the failed links)
and exits 1 when any dataset fails. Without organizations it reads the whole catalogue. Only reads.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests
from completeness import Reader

TIMEOUT = 60
ALWAYS = ["NGSI-LD", "MCP", "WebSocket", "CSV", "JSON",
          "JSON Schema", "JSON-LD", "LinkML", "Markdown", "OWL", "RDF", "SHACL"]
LOCATED = ["GeoJSON", "OGCFEAT"]
BANNED = {"ZIP", "ESRI REST", "SHP", "SHAPEFILE"}


def small(url: str) -> str:
    """`url` asking for one row when it reads an Endpoint's rows; a model artefact stays whole."""
    path = urlsplit(url).path
    if "/api/endpoint/" not in path or "/schema/" in path:
        return url
    joiner = "&" if "?" in url else "?"
    return url + joiner + ("$top=1" if "/odata/" in path else "limit=1")


def banned(resource: dict[str, Any]) -> bool:
    fmt = (resource.get("format") or "").strip().upper()
    return fmt in BANNED or urlsplit(resource.get("url") or "").path.lower().endswith(".zip")


def check(
    package: dict[str, Any],
    get: Callable[[str], requests.Response],
    ws_probe: Callable[[str, str], str | None],
    mcp_probe: Callable[[str], str | None],
    pace: float = 0.0,
) -> dict[str, Any]:
    """The verdict of one dataset: which mandatory formats it lacks, what it lists that is banned,
    which links did not answer, and whether its rows carry a location."""
    resources = package.get("resources") or []
    formats = {r.get("format") for r in resources}
    failed, located = [], False
    for resource in resources:
        url = resource.get("url") or ""
        label = resource.get("format") or resource.get("name") or url
        if url.startswith("wss://"):
            name = resource.get("name", "")
            kind = name[name.rfind("(") + 1:-1] if name.endswith(")") else ""
            problem = ws_probe(url, kind)
        elif url.rstrip("/").endswith("/mcp"):
            problem = mcp_probe(url)
        else:
            try:
                answer = get(small(url))
                problem = None if answer.status_code == 200 else f"{answer.status_code}"
                if problem is None and resource.get("format") == "NGSI-LD":
                    rows = answer.json()
                    located = isinstance(rows, list) and bool(rows) and "location" in rows[0]
            except (requests.RequestException, ValueError) as error:
                problem = type(error).__name__
        if problem:
            failed.append(f"{label} {problem}")
        time.sleep(pace)
    wanted = ALWAYS + (LOCATED if located else [])
    missing = [f for f in wanted if f not in formats]
    listed_banned = sorted({r.get("format") or r.get("url") for r in resources if banned(r)})
    return {"dataset": package["name"], "located": located, "missing": missing,
            "banned": listed_banned, "failed": failed, "ok": not (missing or listed_banned or failed)}


def table(verdicts: list[dict[str, Any]]) -> str:
    columns = ALWAYS + LOCATED
    lines = ["| dataset | located | " + " | ".join(columns) + " | banned | failed links | verdict |",
             "|---|---|" + "---|" * len(columns) + "---|---|---|"]
    for v in verdicts:
        cells = ["—" if f in LOCATED and not v["located"] else ("missing" if f in v["missing"] else "yes")
                 for f in columns]
        lines.append(f"| {v['dataset']} | {'yes' if v['located'] else 'no'} | " + " | ".join(cells)
                     + f" | {', '.join(v['banned']) or '—'} | {'; '.join(v['failed']) or '—'} | "
                     + ("OK" if v["ok"] else "FAIL") + " |")
    passed = sum(v["ok"] for v in verdicts)
    lines += ["", f"datasets: {len(verdicts)}, OK {passed}, FAIL {len(verdicts) - passed}"]
    return "\n".join(lines)


def packages(reader: Reader, organizations: list[str]) -> list[dict[str, Any]]:
    queries = [f"organization:{org}" for org in organizations] or [""]
    out = []
    for fq in queries:
        start = 0
        while True:
            page = reader.action("package_search", fq=fq, rows=1000, start=start)
            out += page["results"]
            start += len(page["results"])
            if not page["results"] or start >= page["count"]:
                break
    return sorted(out, key=lambda p: p["name"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--ckan", required=True)
    parser.add_argument("--pace", type=float, default=0.3, help="seconds between calls")
    parser.add_argument("organizations", nargs="*")
    args = parser.parse_args(argv)
    from surfaces import answers_mcp, answers_ws

    reader = Reader(args.ckan)
    verdicts = [check(p, lambda url: reader.http.get(url, timeout=TIMEOUT),
                      lambda url, kind: answers_ws(reader, url, kind), answers_mcp, args.pace)
                for p in packages(reader, args.organizations)]
    print(table(verdicts))
    return 0 if verdicts and all(v["ok"] for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

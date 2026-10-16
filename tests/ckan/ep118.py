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
    """`url` asking for one row when it pages an Endpoint's rows (NGSI-LD entities, OGC items, an
    OData entity set); a service root, a file and a model artefact are read as listed, since a
    file's gateway route answers 413 to a `limit` it does not take (dev, 2026-10-16)."""
    path = urlsplit(url).path
    if "/api/endpoint/" not in path:
        return url
    joiner = "&" if "?" in url else "?"
    if path.endswith("/entities") or path.endswith("/items"):
        return url + joiner + "limit=1"
    if "/odata/" in path and not path.endswith(("/odata/v4/", "/odata/v4", "/$metadata")):
        return url + joiner + "$top=1"
    return url


def entities_of(url: str, first_type: Callable[[str], str | None]) -> str | None:
    """The one-row entity read behind an NGSI-LD link: the link itself, or for an Endpoint's
    `/ngsi-ld/v1/` root the entities of its first type; None when it has no type."""
    if urlsplit(url).path.endswith("/entities"):
        return url
    kind = first_type(url.split("/ngsi-ld/")[0])
    return f"{url.rstrip('/')}/entities?type={kind}" if kind else None


def banned(resource: dict[str, Any]) -> bool:
    fmt = (resource.get("format") or "").strip().upper()
    return fmt in BANNED or urlsplit(resource.get("url") or "").path.lower().endswith(".zip")


def check(
    package: dict[str, Any],
    get: Callable[[str], requests.Response],
    ws_probe: Callable[[str, str], str | None],
    mcp_probe: Callable[[str], str | None],
    pace: float = 0.0,
    first_type: Callable[[str], str | None] = lambda root: None,
) -> dict[str, Any]:
    """The verdict of one dataset: which mandatory formats it lacks, what it lists that is banned,
    which links did not answer, and whether its rows carry a location."""
    resources = package.get("resources") or []
    formats = {r.get("format") for r in resources}
    failed, located = [], False
    for resource in resources:
        url = resource.get("url") or ""
        label = resource.get("format") or resource.get("name") or url
        # A link that times out, refuses the socket or answers no JSON fails this dataset alone;
        # the sweep goes on to the next one.
        try:
            if url.startswith("wss://") or resource.get("format") == "WebSocket" or url.rstrip("/").endswith("/ws"):
                name = resource.get("name", "")
                kind = name[name.rfind("(") + 1:-1] if name.endswith(")") else ""
                problem = ws_probe(url, kind or first_type(url.rstrip("/").removesuffix("/ws")) or "")
            elif url.rstrip("/").endswith("/mcp"):
                problem = mcp_probe(url)
            else:
                answer = get(small(url))
                problem = None if answer.status_code == 200 else f"{answer.status_code}"
                if problem is None and resource.get("format") == "NGSI-LD":
                    one = entities_of(url, first_type)
                    rows = get(small(one)).json() if one else []
                    located = isinstance(rows, list) and bool(rows) and "location" in rows[0]
        except Exception as error:  # noqa: BLE001 - every cause is a failed link, named
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
    from surfaces import answers_mcp, answers_ws, first_type

    reader = Reader(args.ckan)
    def get(url: str) -> requests.Response:
        # Streamed and closed after the status: a file link is checked, never downloaded.
        with reader.http.get(url, timeout=TIMEOUT, stream=True) as answer:
            if "json" in answer.headers.get("content-type", "") and "/file." not in url:
                answer.content  # noqa: B018 - an entity page is one row, read for its location
            return answer

    verdicts = [check(p, get, lambda url, kind: answers_ws(reader, url, kind), answers_mcp, args.pace,
                      lambda root: first_type(reader, root))
                for p in packages(reader, args.organizations)]
    print(table(verdicts))
    return 0 if verdicts and all(v["ok"] for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

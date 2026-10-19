#!/usr/bin/env python3
"""The request list of api-mix-100rps.js for a whole catalogue (T-4754): every public dataset's
Endpoint and types, read from CKAN, become the questions people ask of them, one row each:

- ngsi      a page of the type, and its count
- geo       the type near one of its own entities (when the type carries a Point)
- temporal  the newest five values of the type's last week (timerel is required, 5.7.4)
- odata     the type's entity set, a page
- ogc       the type's collection, a page
- csv       the type as a file
- mcp       a tools/call of query_entities for the type, five rows
- schema    the Endpoint's artifacts, once per Endpoint
- ckan      one catalogue search per dataset name's first word, at most three
- ws        the Endpoint's WebSocket, once per Endpoint (held by the subscribers)

Each row is probed once with the probed status written beside it, so the mix keeps only what
answers 2xx today (requests.js skips the rest and counts it). The probe is anonymous and paced
under the owner's catalogue limit (50 requests a minute, 500 a run), so it never loads prod.

    python3 make_requests.py https://opendata.smartcity.joinedcontext.com > requests-prod.tsv
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlencode

import requests

TIMEOUT = 20
#: The owner's limit for a catalogue run (T-4743): 50 requests a minute, 500 a run.
PACE_SECONDS = 1.2
MAX_PROBES = 500


def mcp_call(kind: str) -> str:
    """The tools/call an MCP client sends for five rows of `kind`."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_entities", "arguments": {"type": [kind], "limit": 5}},
        },
        separators=(",", ":"),
    )


def point_of(entity: dict[str, Any] | None) -> tuple[float, float] | None:
    """The Point an entity in keyValues carries under `location`, else None."""
    location = (entity or {}).get("location")
    if not isinstance(location, dict) or location.get("type") != "Point":
        return None
    coordinates = location.get("coordinates")
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in coordinates[:2])
    ):
        return float(coordinates[0]), float(coordinates[1])
    return None


def rows_for(
    endpoint: str, kind: str, point: tuple[float, float] | None, since: str = "2026-01-01T00:00:00Z"
) -> list[tuple[str, str, str, str]]:
    """The questions of one type on one Endpoint: (kind, method, url, body); `since` starts the
    history the temporal question reads."""
    base = endpoint.rstrip("/") + "/"
    typed = quote(kind, safe="")
    entities = f"{base}ngsi-ld/v1/entities"
    rows = [
        ("ngsi", "GET", f"{entities}?{urlencode({'type': kind, 'limit': 20})}", ""),
        ("ngsi", "GET", f"{entities}?{urlencode({'type': kind, 'count': 'true', 'limit': 0})}", ""),
        ("temporal", "GET", f"{base}ngsi-ld/v1/temporal/entities?{urlencode({'type': kind, 'timerel': 'after', 'timeAt': since, 'lastN': 5, 'limit': 5})}", ""),
        ("odata", "GET", f"{base}odata/v4/{typed}?{urlencode({'$top': 20})}", ""),
        ("ogc", "GET", f"{base}ogc/features/collections/{typed}/items?{urlencode({'limit': 20})}", ""),
        ("csv", "GET", f"{base}file.csv?{urlencode({'type': kind})}", ""),
        ("mcp", "POST", f"{base}mcp", mcp_call(kind)),
    ]
    if point is not None:
        near = {
            "type": kind,
            "georel": "near;maxDistance==2000",
            "geometry": "Point",
            "coordinates": json.dumps([point[0], point[1]], separators=(",", ":")),
            "limit": 20,
        }
        rows.insert(2, ("geo", "GET", f"{entities}?{urlencode(near)}", ""))
    return rows


def endpoint_rows(endpoint: str) -> list[tuple[str, str, str, str]]:
    """What is asked once per Endpoint: its artifacts and its WebSocket."""
    base = endpoint.rstrip("/") + "/"
    socket = "wss://" + base.split("://", 1)[1] if base.startswith("https://") else "ws://" + base.split("://", 1)[1]
    return [("schema", "GET", f"{base}schema/index.json", ""), ("ws", "GET", f"{socket}ws", "")]


def datasets_of(answer: dict[str, Any]) -> list[tuple[str, str, list[str]]]:
    """(name, Endpoint, types) of each dataset in a package_search result that names an Endpoint;
    its types are the names of its DataStore tables, as the CKAN publisher writes them."""
    found = []
    for package in (answer.get("result") or {}).get("results") or []:
        extras = {e.get("key"): e.get("value") for e in package.get("extras") or []}
        endpoint = (extras.get("endpoint") or "").strip()
        if not endpoint.startswith(("https://", "http://")):
            continue
        kinds = [r["name"] for r in package.get("resources") or [] if r.get("datastore_active") and r.get("name")]
        if kinds:
            found.append((package.get("name") or "", endpoint, kinds))
    return found


class Prober:
    """Anonymous, paced, bounded: one call at a time, `pace` seconds apart, at most `limit`."""

    def __init__(self, session: requests.Session, pace: float, limit: int, sleep: Callable[[float], None] = time.sleep):
        self.session, self.pace, self.limit, self.sleep = session, pace, limit, sleep
        self.spent = 0

    def call(self, method: str, url: str, body: str = "") -> requests.Response | None:
        if self.spent >= self.limit:
            return None
        if self.spent:
            self.sleep(self.pace)
        self.spent += 1
        try:
            if method == "POST":
                headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
                return self.session.post(url, data=body, headers=headers, timeout=TIMEOUT)
            if url.startswith(("ws://", "wss://")):
                # A WebSocket is held by the subscribers; its row is not probed over HTTP.
                return None
            return self.session.get(url, timeout=TIMEOUT)
        except requests.RequestException:
            return None


def status_of(answer: requests.Response | None) -> str:
    return str(answer.status_code) if answer is not None else ""


def first_point(prober: Prober, endpoint: str, kind: str) -> tuple[float, float] | None:
    url = endpoint.rstrip("/") + "/ngsi-ld/v1/entities?" + urlencode(
        {"type": kind, "limit": 1, "attrs": "location", "format": "keyValues"}
    )
    answer = prober.call("GET", url)
    if answer is None or answer.status_code != 200:
        return None
    try:
        entities = answer.json()
    except ValueError:
        return None
    return point_of(entities[0] if isinstance(entities, list) and entities else None)


def build(ckan: str, prober: Prober) -> Iterable[str]:
    """The list's lines: a header comment, then every row with its probed status."""
    search = prober.call("GET", f"{ckan.rstrip('/')}/api/3/action/package_search?rows=1000")
    if search is None or search.status_code != 200:
        raise SystemExit(f"{ckan}: package_search answered {status_of(search) or 'nothing'}")
    datasets = datasets_of(search.json())
    yield f"# T-4754 mix from {ckan}: {len(datasets)} datasets with an Endpoint, probed {time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())}"
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))
    seen: set[str] = set()
    words: list[str] = []
    for name, endpoint, kinds in datasets:
        if prober.spent >= prober.limit:
            yield f"# the probe stopped at {prober.limit} calls; the datasets after {name!r} are not in the list"
            break
        rows: list[tuple[str, str, str, str]] = []
        if endpoint not in seen:
            seen.add(endpoint)
            rows += endpoint_rows(endpoint)
        for kind in kinds:
            rows += rows_for(endpoint, kind, first_point(prober, endpoint, kind), since)
        for kind, method, url, body in rows:
            if kind == "ws":
                yield "\t".join([kind, method, url])
                continue
            answer = prober.call(method, url, body)
            if answer is None and prober.spent >= prober.limit:
                break
            # A row the probe could not reach is kept with status 0, which the parser skips.
            yield "\t".join([kind, method, url, status_of(answer) or "0", body]).rstrip("\t")
        word = name.split("-", 1)[0]
        if word and word not in words and len(words) < 3:
            words.append(word)
    for word in words:
        url = f"{ckan.rstrip('/')}/api/3/action/package_search?{urlencode({'q': word, 'rows': 10})}"
        answer = prober.call("GET", url)
        if answer is not None:
            yield "\t".join(["ckan", "GET", url, status_of(answer)])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ckan", help="the catalogue, e.g. https://opendata.smartcity.joinedcontext.com")
    parser.add_argument("--pace", type=float, default=PACE_SECONDS, help="seconds between probes (default 1.2: 50 a minute)")
    parser.add_argument("--max", type=int, default=MAX_PROBES, help="probes at most (default 500)")
    args = parser.parse_args(argv)
    if args.pace < PACE_SECONDS or not 0 < args.max <= MAX_PROBES:
        parser.error(f"--pace below {PACE_SECONDS} s or --max outside 1..{MAX_PROBES} passes the owner's limit")
    for line in build(args.ckan, Prober(requests.Session(), args.pace, args.max)):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

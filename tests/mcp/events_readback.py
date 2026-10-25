#!/usr/bin/env python3
"""Read a city's events back through the public MCP connector and compare them with their sources (T-5666).

For each city: list_endpoints finds the city's events Endpoint by its space and title, query_entities reads its
upcoming Events (keyValues, the source attribute only), the events are counted per source, and one event of each
of the N largest sources is compared with what its source publishes: Helsinki's with the Linked Events record of
the same id (name and start), the others with the event's own page (its name appears in the page text). One
request per sampled source, the inventory User-Agent, a 30 s timeout.

    python3 tests/mcp/events_readback.py helsinki bratislava praha [--sources 5]

Exit 1 when a city's Endpoint is missing, holds no upcoming event, or a sample does not match its source.
`MCP_HUB_URL` overrides https://api.dev.joinedcontext.com/api/mcp.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from collections import Counter

HUB = os.environ.get("MCP_HUB_URL", "https://api.dev.joinedcontext.com/api/mcp")
UA = "joinedcontext-platform/1.0 (+https://dev.joinedcontext.com; public events)"
#: city -> (space, Endpoint title, the attribute that names an event's source, the attributes read)
CITIES = {
    "helsinki": ("helsinki", "Helsinki events", "dataProvider", ["name", "startDate", "dataProvider"]),
    "bratislava": ("slovensko-podujatia", "Events in Bratislava", "source", ["name", "startDate", "url", "source"]),
    "praha": ("praha-akce", "Events in Prague", "source", ["name", "startDate", "url", "source"]),
}


class Hub:
    def __init__(self, url: str) -> None:
        self.url, self.session, self.n = url, None, 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "events-readback", "version": "1"}})

    def rpc(self, method: str, params: dict) -> dict:
        self.n += 1
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-06-18"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.url, body, headers), timeout=60) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            text = r.read(32 << 20).decode()
        if not text.lstrip().startswith("{"):
            text = [line[5:] for line in text.splitlines() if line.startswith("data:")][-1]
        return json.loads(text)

    def tool(self, name: str, arguments: dict) -> str:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        text = result["content"][0]["text"]
        if result.get("isError"):
            raise RuntimeError(f"{name}: {text}")
        return text


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(2 << 20).decode("utf-8", "replace")


def words(text: str) -> str:
    return " ".join(re.findall(r"\w+", html.unescape(text).lower()))


def matches(event: dict, source_attr: str) -> bool:
    name = event.get("name")
    if isinstance(name, dict):  # a LanguageProperty in keyValues: {"languageMap": {"fi": ..., "en": ...}}
        name = next(iter(name.get("languageMap", name).values()), "")
    name = str(name or "")
    if source_attr == "dataProvider":
        record = json.loads(fetch("https://api.hel.fi/linkedevents/v1/event/"
                                  + event["id"].rsplit(":", 1)[-1].replace("-", ":", 1) + "/"))
        return name in (record.get("name") or {}).values() and record["start_time"][:16] == event["startDate"][:16]
    return bool(event.get("url")) and words(name) in words(fetch(event["url"]))


def city(hub: Hub, key: str, sources: int) -> bool:
    space, title, attr, pick = CITIES[key]
    rows = [line.split(" | ") for line in hub.tool("list_endpoints", {"space": space}).splitlines()[1:]]
    slug = next((r[0] for r in rows if len(r) > 1 and r[1] == title), None)
    if slug is None:
        print(f"{key}: no Endpoint titled {title!r} in space {space}")
        return False
    today = dt.date.today().isoformat()
    events, cursor = [], None
    while True:
        args = {"endpoint": slug, "type": ["Event"], "format": "keyValues", "limit": 1000,
                "q": f"startDate>={today}", "attrs": pick}
        if cursor:
            args["cursor"] = cursor
        page = json.loads(hub.tool("query_entities", args))
        events += page.get("entities", [])
        cursor = page.get("nextCursor")
        if not cursor:
            break
    per = Counter(str(e.get(attr)) for e in events)
    print(f"{key}: Endpoint {slug}, {len(events)} upcoming events from {len(per)} sources")
    ok = bool(events)
    for source, n in per.most_common(sources):
        sample = next(e for e in events if str(e.get(attr)) == source)
        try:
            good = matches(sample, attr)
        except (OSError, ValueError, KeyError) as e:  # a source that does not answer is a failed comparison, named
            good = False
            print(f"  {source}: {type(e).__name__}")
        ok &= good
        print(f"  {source}: {n} events; {sample['id']} {'matches' if good else 'DIFFERS from'} its source")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cities", nargs="+", choices=sorted(CITIES))
    parser.add_argument("--sources", type=int, default=5)
    a = parser.parse_args()
    hub = Hub(HUB)
    results = [city(hub, c, a.sources) for c in a.cities]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

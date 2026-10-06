"""Does every dataset's MCP resource work for an AI assistant (T-3152, EP-81, AG-05, SP-20)?

An AI assistant reaches a catalogue's data through each dataset's MCP resource. For every dataset
of the catalogue (or the ones named), anonymously and with no model call:

- **listed**: the dataset carries one MCP resource whose description says what an AI client
  can do with it: the tools, the endpoint it reads, and the licence the data is under;
- **initialize** and **tools/list** answer over the public URL without a login, and every tool
  has a description and a parameter schema a client can form a call from;
- **rows**: one `query_entities` per DataStore table (one per entity type) answers rows of that
  type, and its `total` equals the rows CKAN's table holds;
- **errors are readable**: a malformed query answers `isError` with a sentence, not a crash;
- **client config**: the dataset page shows an MCP client configuration whose `url` is this
  resource's URL, so a stock client works from what the page shows.

A dataset whose endpoint is restricted must expose no MCP resource that answers anonymously.

    python3 tests/ckan/mcp_check.py https://data.dev.joinedcontext.com [dataset ...]

prints a table per dataset and exits 1 on any FAIL. It sends no token, so none is printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import requests

TIMEOUT = 60
PROTOCOL = "2025-06-18"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
#: The tools an assistant needs to read a dataset; others may come and go.
NEEDED = ("query_entities", "get_entity", "describe_schema")


@dataclass
class Report:
    dataset: str
    rows: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, check: str, ok: bool, detail: str) -> None:
        self.rows.append((check, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)

    def markdown(self) -> str:
        lines = [f"### {self.dataset}", "", "| check | result | detail |", "|---|---|---|"]
        for check, ok, detail in self.rows:
            lines.append(f"| {check} | {'PASS' if ok else 'FAIL'} | {detail.replace('|', '/')} |")
        return "\n".join(lines)


def rpc(url: str, method: str, params: dict[str, Any] | None = None, ident: int = 1) -> tuple[int, Any]:
    """One JSON-RPC call; the HTTP status and the decoded body (an SSE `data:` line included)."""
    body = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        body["params"] = params
    answer = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
    text = answer.text
    if answer.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        text = data[-1] if data else ""
    try:
        return answer.status_code, json.loads(text) if text else None
    except ValueError:
        return answer.status_code, None


def describes(description: str, endpoint: str, licences: list[str]) -> list[str]:
    """What a resource description an AI client acts on still lacks; the licence may be named by
    any of its names (CKAN's title with or without its abbreviation, or its id)."""
    lacks = []
    text = description.lower()
    if not re.search(r"\btools?\b", text):
        lacks.append("the tools")
    if endpoint and endpoint.rstrip("/").lower() not in text:
        lacks.append("the endpoint")
    names = {re.sub(r"\s*\([^)]*\)\s*$", "", name).strip().lower() for name in licences if name}
    if names and not any(name in text for name in names):
        lacks.append("the licence")
    return lacks


def tool_problems(tools: list[dict[str, Any]]) -> list[str]:
    """Tools a client could not form a call from: no description, or no object schema."""
    problems = []
    names = {tool.get("name") for tool in tools}
    problems += [f"no tool {name}" for name in NEEDED if name not in names]
    for tool in tools:
        schema = tool.get("inputSchema") or {}
        if not (tool.get("description") or "").strip():
            problems.append(f"{tool.get('name')} has no description")
        if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
            problems.append(f"{tool.get('name')} has no parameter schema")
    return problems


def client_config(html: str, url: str) -> str | None:
    """Why the dataset page's MCP client configuration would not reach `url`, or None when it does."""
    for block in re.findall(r"<(?:pre|code)[^>]*>(.*?)</(?:pre|code)>", html, flags=re.S):
        text = re.sub(r"<[^>]+>", "", block).replace("&#34;", '"').replace("&quot;", '"').replace("&amp;", "&")
        if "mcpServers" not in text:
            continue
        try:
            servers = json.loads(text)["mcpServers"]
        except (ValueError, KeyError, TypeError):
            return "the page's configuration is not JSON with mcpServers"
        urls = [server.get("url") for server in servers.values() if isinstance(server, dict)]
        return None if url in urls else f"the configuration names {urls}, not {url}"
    return "the page shows no MCP client configuration"


def check(ckan: str, name: str) -> Report:
    report = Report(name)
    action = lambda what, **params: requests.get(f"{ckan}/api/3/action/{what}", params=params, timeout=TIMEOUT).json()["result"]
    package = action("package_show", id=name)
    extras = {e["key"]: e["value"] for e in package.get("extras") or []}
    endpoint = (extras.get("endpoint") or "").rstrip("/")
    licences = [package.get("license_title") or "", package.get("license_id") or "", package.get("license_url") or "", extras.get("license_url") or ""]
    resources = package.get("resources") or []
    mcp = [r for r in resources if (r.get("format") or "").upper() == "MCP"]
    if len(mcp) != 1:
        report.add("listed", False, f"{len(mcp)} MCP resources")
        return report
    resource = mcp[0]
    url = resource["url"]
    lacks = describes(resource.get("description") or "", endpoint, licences)
    report.add("listed", not lacks, "description names the tools, the endpoint and the licence" if not lacks else f"the description lacks {', '.join(lacks)}")

    status, init = rpc(url, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "jc-mcp-check", "version": "1"}})
    if status == 401:
        report.add("initialize", True, "401: a restricted endpoint answers no anonymous client")
        return report
    ok = status == 200 and isinstance(init, dict) and "result" in init
    report.add("initialize", ok, f"{status}, {init.get('result', {}).get('serverInfo', {}).get('name', '?') if ok else init}")
    if not ok:
        return report
    status, listed = rpc(url, "tools/list", ident=2)
    tools = (listed or {}).get("result", {}).get("tools", []) if status == 200 else []
    problems = tool_problems(tools)
    report.add("tools/list", status == 200 and not problems, f"{len(tools)} tools" if not problems else "; ".join(problems))

    for table in (r for r in resources if r.get("datastore_active")):
        kind = table.get("name") or "?"
        held = action("datastore_search", resource_id=table["id"], limit=0)["total"]
        status, answer = rpc(url, "tools/call", {"name": "query_entities", "arguments": {"type": kind, "limit": 3, "count": True, "format": "keyValues"}}, ident=3)
        result = (answer or {}).get("result", {})
        content = result.get("structuredContent") or {}
        rows = content.get("entities") or []
        total = content.get("total")
        good = status == 200 and not result.get("isError") and rows and all(row.get("type") == kind for row in rows) and total == held
        report.add(f"{kind}: rows", bool(good), f"MCP total {total}, CKAN {held}, {len(rows)} rows of {kind}" if status == 200 else f"{status}")

    status, wrong = rpc(url, "tools/call", {"name": "query_entities", "arguments": {"type": "Unknown", "q": "((("}}, ident=4)
    result = (wrong or {}).get("result", {})
    said = " ".join(c.get("text", "") for c in result.get("content", []))
    report.add("errors readable", status == 200 and result.get("isError") is True and len(said) > 10, said[:80] or f"{status}")

    page = requests.get(f"{ckan}/dataset/{name}", timeout=TIMEOUT).text
    resource_page = requests.get(f"{ckan}/dataset/{name}/resource/{resource['id']}", timeout=TIMEOUT).text
    why = client_config(page, url) and client_config(resource_page, url)
    report.add("client config", why is None, "the page's configuration reaches this resource" if why is None else why)
    return report


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ckan_url")
    parser.add_argument("datasets", nargs="*")
    args = parser.parse_args(argv)
    ckan = args.ckan_url.rstrip("/")
    names = args.datasets or [
        p["name"] for p in requests.get(f"{ckan}/api/3/action/package_search", params={"rows": 1000, "fl": "name"}, timeout=TIMEOUT).json()["result"]["results"]
    ]
    failed = False
    for name in names:
        report = check(ckan, name)
        print(report.markdown(), end="\n\n")
        failed |= not report.ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

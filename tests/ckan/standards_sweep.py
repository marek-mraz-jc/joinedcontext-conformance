"""Does every converted open dataset answer in every standard its catalogue page lists (T-4279)?

For each dataset of the organizations named, read anonymously from CKAN, every resource the
harvest wrote as `resource_type: api` (deployment charts/ckan/files/od-standards.py) is called
over its own URL, once, paced: an HTTP `GET` (200, a body of the media type it declares, and how
many rows: NGSI-LD from `NGSILD-Results-Count`, GeoJSON features, OData `value`, JSON items, CSV
lines; XLSX by its size), the WebSocket by an `x-subscribe` answered 201, MCP by `initialize`.

    python3 tests/ckan/standards_sweep.py --ckan https://data.dev.joinedcontext.com bbsk banskabystrica

prints one Markdown table row per distribution and a tally, and exits 1 when any is neither OK
nor RESTRICTED (a 401: the sweep reads anonymously, so a closed distribution answers 401 by
design, T-4386). A 429 from the edge's rate limit is asked again after a wait and is not a verdict
until it outlasts every wait. A dataset with no api resource is listed too, as `none`: nothing
converted it, or the harvest has not run since the mapping named it. `--out` writes the table to a
file, `--junit` one test case per row, which the hourly batch files a task for when it fails
(`tests/ckan/sweep.sh`). Only reads; no token.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from typing import Any

import requests
from completeness import Reader

TIMEOUT = 60
# What passes: the distribution answered, or it is closed to an anonymous reader (T-4386).
PASSING = ("OK", "RESTRICTED")
# The waits after a 429 that names no Retry-After, in seconds; one names its own, up to the cap.
THROTTLE_WAITS = (2.0, 10.0, 30.0)
RETRY_AFTER_CAP = 60.0


def rows(url: str, media: str, body: bytes, headers: dict[str, str]) -> int | None:
    """How many rows `body` carries, or None when it is not what `media` says."""
    if media.endswith("spreadsheetml.sheet"):
        return len(body) if body.startswith(b"PK") else None
    if media == "text/csv":
        lines = list(csv.reader(io.StringIO(body.decode("utf-8-sig", "replace"))))
        return max(len(lines) - 1, 0)
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    if "/ngsi-ld/" in url:
        count = headers.get("NGSILD-Results-Count")
        return int(count) if count and count.isdigit() else (len(doc) if isinstance(doc, list) else None)
    if isinstance(doc, dict) and isinstance(doc.get("features"), list):
        return len(doc["features"])
    if isinstance(doc, dict) and isinstance(doc.get("value"), list):
        return len(doc["value"])
    return len(doc) if isinstance(doc, list) else None


def retry_after(answer: Any, default: float) -> float:
    """The wait a 429 asks for in seconds, `default` when it names none, never over the cap."""
    value = (answer.headers.get("retry-after") or answer.headers.get("Retry-After") or "").strip()
    return min(float(value), RETRY_AFTER_CAP) if value.isdigit() else default


def probe_http(
    http: requests.Session,
    resource: dict[str, Any],
    waits: tuple[float, ...] = THROTTLE_WAITS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """(verdict, detail) of one HTTP distribution."""
    url, media = resource["url"], resource.get("mimetype") or ""
    asked = url + ("&count=true" if "/ngsi-ld/" in url else "")
    for wait in (*waits, None):
        try:
            answer = http.get(asked, timeout=TIMEOUT)
        except requests.RequestException as error:
            return "FAIL", type(error).__name__
        if answer.status_code != 429 or wait is None:
            break
        sleep(retry_after(answer, wait))
    kind = answer.headers.get("content-type", "").split(";")[0].strip()
    if answer.status_code == 429:
        return "FAIL", f"429 after {len(waits) + 1} tries"
    if answer.status_code == 401:
        return "RESTRICTED", "401"
    if answer.status_code != 200:
        problem = ""
        if kind.endswith("json"):
            try:
                problem = f" {answer.json().get('title') or answer.json().get('type') or ''}".rstrip()
            except (ValueError, AttributeError):
                pass
        return "FAIL", f"{answer.status_code}{problem}"
    if media and kind != media:
        return "FAIL", f"200 {kind}, listed as {media}"
    count = rows(url, media, answer.content, answer.headers)
    if count is None:
        return "FAIL", f"200 {kind}, unreadable body"
    if media.endswith("spreadsheetml.sheet"):
        return "OK", f"200 {count} bytes"
    return ("OK" if count else "EMPTY"), f"200 {count} rows"


def entity_type(resource: dict[str, Any]) -> str:
    """The type in the resource name, `WebSocket (Supplier)` → `Supplier`."""
    name = resource.get("name", "")
    return name[name.rfind("(") + 1:-1] if name.endswith(")") else ""


def sweep(
    packages: list[dict[str, Any]],
    http_probe: Callable[[dict[str, Any]], tuple[str, str]],
    ws_probe: Callable[[str, str], str | None],
    mcp_probe: Callable[[str], str | None],
    pace: float = 0.0,
) -> list[tuple[str, str, str, str]]:
    """(dataset, distribution, verdict, detail) for every api resource of every package."""
    out = []
    for package in sorted(packages, key=lambda p: p["name"]):
        apis = [r for r in package.get("resources", []) if r.get("resource_type") == "api"]
        if not apis:
            out.append((package["name"], "-", "none", "no api distribution"))
        for resource in apis:
            label = resource.get("name", resource["url"]).split(" (")[0]
            if resource["url"].startswith("wss://"):
                problem = ws_probe(resource["url"], entity_type(resource))
                verdict, detail = ("OK", "x-subscribe 201") if problem is None else ("FAIL", problem)
            elif resource["url"].endswith("/mcp"):
                problem = mcp_probe(resource["url"])
                verdict, detail = ("OK", "initialize 200") if problem is None else ("FAIL", problem)
            else:
                verdict, detail = http_probe(resource)
            out.append((package["name"], label, verdict, detail))
            time.sleep(pace)
    return out


def table(results: list[tuple[str, str, str, str]]) -> str:
    lines = ["| dataset | distribution | verdict | answer |", "|---|---|---|---|"]
    lines += [f"| {d} | {s} | {v} | {a} |" for d, s, v, a in results]
    tally = Counter(v for _, _, v, _ in results)
    per = Counter((s, v) for _, s, v, _ in results if s != "-")
    lines += ["", "tally: " + ", ".join(f"{v} {n}" for v, n in sorted(tally.items()))]
    lines += ["per distribution: " + ", ".join(f"{s} {v} {n}" for (s, v), n in sorted(per.items()))]
    return "\n".join(lines)


def junit(results: list[tuple[str, str, str, str]]) -> bytes:
    """One test case per row, failing unless its verdict passes: a red row is one filed task."""
    suite = ET.Element("testsuite", name="standards_sweep", tests=str(len(results)),
                       failures=str(sum(v not in PASSING for _, _, v, _ in results)))
    for dataset, distribution, verdict, detail in results:
        case = ET.SubElement(suite, "testcase", classname="standards_sweep", name=f"{dataset} {distribution}")
        if verdict not in PASSING:
            ET.SubElement(case, "failure", message=f"{verdict}: {detail}")
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--ckan", required=True)
    parser.add_argument("--pace", type=float, default=0.3, help="seconds between calls")
    parser.add_argument("--out", help="also write the table to this file")
    parser.add_argument("--junit", help="write one JUnit test case per row to this file")
    parser.add_argument("organizations", nargs="+")
    args = parser.parse_args(argv)
    from surfaces import answers_mcp, answers_ws

    reader = Reader(args.ckan)
    packages = []
    for org in args.organizations:
        packages += reader.action("package_search", fq=f"organization:{org}", rows=1000)["results"]
    results = sweep(packages, lambda r: probe_http(reader.http, r),
                    lambda url, kind: answers_ws(reader, url, kind), answers_mcp, args.pace)
    report = table(results)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            out.write(report + "\n")
    if args.junit:
        with open(args.junit, "wb") as out:
            out.write(junit(results))
    return 0 if all(v in PASSING for _, _, v, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

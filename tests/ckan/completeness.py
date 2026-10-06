"""Is each DataStore table of a published dataset complete, correct and current (T-3012, T-3112…T-3120)?

For every DataStore resource of one CKAN dataset (one table per entity type, named by the type):

- **rows**: its total equals the entity count of that type the dataset's Endpoint answers
  (`count=true`, `NGSILD-Results-Count`);
- **columns**: every attribute the type's class declares in the Endpoint's `model.schema.json`
  has a column (a column's stem is its name up to the first `.` or `[`), the completeness diff of
  T-3012;
- **values**: a sample of rows equals the broker's entity, attribute by attribute. A value the
  broker has since observed again (a later `observedAt`, or a later `dateModified`) moved on and
  is counted as such, not as wrong: a live feed changes between a refresh and this read;
- **current**: the newest timestamp the table holds is within `max_age`.

Then every other resource answers (200, or 401 for a restricted Endpoint, EP-64) with content of
its kind and a name of its own, and the dataset page carries what a reader needs. Only reads.

    python3 tests/ckan/completeness.py https://data.dev.joinedcontext.com helsinki-bikes [...]

prints one table per dataset and exits 1 when any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote

import requests

TIMEOUT = 60
SAMPLE = 20
#: Columns whose newest value says when a table was last fed.
TIME_SUFFIXES = (".observedAt", "dateModified.value", "dateObserved.value", "updatedAt.value", "modifiedAt")
STEM = re.compile(r"[.\[]")


def stem(column: str) -> str:
    """The attribute a column carries: `location.value.coordinates[0]` is `location`."""
    return STEM.split(column, maxsplit=1)[0]


def missing_attributes(columns: list[str], properties: dict[str, Any]) -> list[str]:
    """The attributes the class declares that no column carries (T-3012's diff)."""
    stems = {stem(column) for column in columns}
    return sorted(name for name in properties if name not in ("id", "type") and name not in stems)


def cell_of(entity: dict[str, Any], column: str) -> tuple[bool, Any]:
    """The value `column` names in a normalized NGSI-LD entity, and whether it has one.

    `attr.value`, `attr.observedAt`, `attr.unitCode`, `attr.object`, `attr.languageMap.fi`,
    `attr.value.member`, `attr.value.coordinates[0]`; a `.geojson` column is the GeoJSON text of a
    geometry and is compared by the geometry it parses to.
    """
    path = re.findall(r"[^.\[\]]+|\[\d+\]", column)
    if not path:
        return False, None
    current: Any = entity
    for part in path:
        if part.startswith("["):
            index = int(part[1:-1])
            if not isinstance(current, list) or index >= len(current):
                return False, None
            current = current[index]
        elif part == "geojson" and isinstance(current, dict):
            current = current.get("value")
        else:
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
    return True, current


def same(row_value: Any, broker_value: Any) -> bool:
    """Whether a DataStore cell holds the broker's value, as CKAN types it."""
    if isinstance(row_value, str) and isinstance(broker_value, (dict, list)):
        try:
            return json.loads(row_value) == broker_value
        except ValueError:
            return False
    if isinstance(row_value, (int, float)) and isinstance(broker_value, (int, float)):
        return abs(float(row_value) - float(broker_value)) <= 1e-9 * max(1.0, abs(float(broker_value)))
    if isinstance(broker_value, (dict, list)):
        return row_value == broker_value
    if isinstance(row_value, str) and isinstance(broker_value, str) and "T" in broker_value:
        return parse_time(row_value) == parse_time(broker_value) or row_value == broker_value
    return row_value == broker_value or str(row_value) == str(broker_value)


def parse_time(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def moved_on(row: dict[str, Any], entity: dict[str, Any], attribute: str) -> bool:
    """Whether the broker observed `attribute` (or the entity) again after the row was written."""
    for suffix in (f"{attribute}.observedAt", "dateModified.value"):
        before = parse_time(row.get(suffix))
        _, now = cell_of(entity, suffix)
        after = parse_time(now)
        if before and after and after > before:
            return True
    return False


def compare(row: dict[str, Any], entity: dict[str, Any]) -> tuple[list[str], int]:
    """The columns where `row` differs from the broker's `entity`, and how many moved on."""
    wrong: list[str] = []
    moved = 0
    for column, value in row.items():
        if column in ("_id", "entity_id", "type", "_full_text") or value is None:
            continue
        has, broker = cell_of(entity, column)
        if not has:
            wrong.append(f"{column}: the broker has none, the table {value!r}")
            continue
        if same(value, broker):
            continue
        if moved_on(row, entity, stem(column)):
            moved += 1
            continue
        wrong.append(f"{column}: table {value!r}, broker {broker!r}")
    return wrong, moved


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    dataset: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def markdown(self) -> str:
        lines = [f"### {self.dataset}", "", "| check | result | detail |", "|---|---|---|"]
        for check in self.checks:
            detail = check.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {check.name} | {'PASS' if check.ok else 'FAIL'} | {detail} |")
        return "\n".join(lines)


class Reader:
    """Reads CKAN and the Endpoint behind a dataset, anonymously unless given a bearer."""

    def __init__(self, ckan_url: str, bearer: str | None = None) -> None:
        self.ckan = ckan_url.rstrip("/")
        self.http = requests.Session()
        self.bearer = bearer

    def get(self, url: str, **params: Any) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.bearer}"} if self.bearer else {}
        return self.http.get(url, params=params or None, headers=headers, timeout=TIMEOUT)

    def action(self, action: str, **params: Any) -> Any:
        answer = self.http.get(f"{self.ckan}/api/3/action/{action}", params=params, timeout=TIMEOUT)
        body = answer.json() if answer.headers.get("content-type", "").startswith("application/json") else {}
        if answer.status_code >= 400 or not body.get("success"):
            raise RuntimeError(f"{action} answered {answer.status_code}: {body.get('error')}")
        return body["result"]


def check_dataset(
    reader: Reader,
    name: str,
    max_age: timedelta,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ages: dict[str, timedelta] | None = None,
) -> Report:
    """`ages` overrides `max_age` per table (its entity type): each table follows its own
    pipeline's schedule, an hourly feed beside a weekly register in one dataset."""
    report = Report(name)
    try:
        package = reader.action("package_show", id=name)
    except RuntimeError as error:
        report.add("dataset", False, str(error))
        return report
    extras = {extra["key"]: extra["value"] for extra in package.get("extras") or []}
    page = {
        "title": package.get("title"),
        "description": package.get("notes"),
        "licence": package.get("license_id"),
        "organisation": (package.get("organization") or {}).get("name"),
        "tags": package.get("tags"),
        "publisher": extras.get("publisher_name"),
        "frequency": extras.get("frequency"),
    }
    empty = [key for key, value in page.items() if not value]
    report.add("dataset page", not empty, "every field filled" if not empty else f"empty: {', '.join(empty)}")
    endpoint = (extras.get("endpoint") or "").rstrip("/")

    schema: dict[str, Any] = {}
    if endpoint:
        answer = reader.get(f"{endpoint}/schema/v1/model.schema.json")
        if answer.status_code == 200:
            schema = answer.json()
    definitions = schema.get("$defs") or schema.get("definitions") or {}

    resources = package.get("resources") or []
    tables = [r for r in resources if r.get("datastore_active")]
    report.add("DataStore tables", bool(tables), f"{len(tables)} table(s)")
    for resource in tables:
        age = (ages or {}).get(resource.get("name") or "", max_age)
        check_table(reader, report, resource, endpoint, definitions, age, now())

    names = [r.get("name") for r in resources]
    twice = sorted({n for n in names if names.count(n) > 1})
    report.add("resource names", not twice, "each its own" if not twice else f"twice: {', '.join(map(str, twice))}")
    for resource in resources:
        if resource.get("datastore_active"):
            continue
        check_link(reader, report, resource)
    return report


def check_table(
    reader: Reader,
    report: Report,
    resource: dict[str, Any],
    endpoint: str,
    definitions: dict[str, Any],
    max_age: timedelta,
    now: datetime,
) -> None:
    kind = resource.get("name") or "?"
    first = reader.action("datastore_search", resource_id=resource["id"], limit=0)
    total = first["total"]
    columns = [f["id"] for f in first["fields"] if f["id"] != "_id"]

    if endpoint:
        answer = reader.get(f"{endpoint}/ngsi-ld/v1/entities", type=kind, count="true", limit=0)
        broker = answer.headers.get("NGSILD-Results-Count")
        if answer.status_code == 200 and broker is not None:
            report.add(f"{kind}: rows", int(broker) == total, f"CKAN {total}, broker {broker}")
        else:
            report.add(f"{kind}: rows", False, f"the endpoint answered {answer.status_code} for the count")
    else:
        report.add(f"{kind}: rows", False, "the dataset names no endpoint to count against")

    properties = (definitions.get(kind) or {}).get("properties") or {}
    if properties:
        missing = missing_attributes(columns, properties)
        report.add(
            f"{kind}: columns",
            not missing,
            f"{len(properties)} attributes covered" if not missing else f"no column for {', '.join(missing)}",
        )
    else:
        report.add(f"{kind}: columns", False, f"model.schema.json declares no class {kind}")

    stamps = [c for c in columns if c.endswith(TIME_SUFFIXES)]
    newest: datetime | None = None
    for column in stamps:
        top = reader.action("datastore_search", resource_id=resource["id"], limit=1, sort=f'"{column}" desc nulls last')
        for record in top["records"]:
            moment = parse_time(record.get(column))
            if moment and (newest is None or moment > newest):
                newest = moment
    if newest is None:
        # A register without a time attribute (schools, bridges) is current when it equals the
        # broker, which the rows and values checks hold it to.
        report.add(f"{kind}: current", True, "no time attribute: current as long as it equals the broker")
    else:
        age = now - newest
        report.add(f"{kind}: current", age <= max_age, f"newest {newest.isoformat()} ({age.total_seconds() / 3600:.1f} h old)")

    if not endpoint or total == 0:
        return
    sample = reader.action("datastore_search", resource_id=resource["id"], limit=SAMPLE)["records"]
    wrong: list[str] = []
    moved = 0
    compared = 0
    for row in sample:
        answer = reader.get(f"{endpoint}/ngsi-ld/v1/entities/{quote(row['entity_id'], safe='')}")
        if answer.status_code != 200:
            wrong.append(f"{row['entity_id']}: the broker answered {answer.status_code}")
            continue
        differs, later = compare(row, answer.json())
        compared += 1
        moved += later
        wrong.extend(f"{row['entity_id']} {d}" for d in differs)
    report.add(
        f"{kind}: values",
        not wrong,
        f"{compared} rows equal the broker ({moved} value(s) moved on since)" if not wrong else "; ".join(wrong[:5]),
    )


#: What a resource of each format must parse as.
JSON_FORMATS = {"JSON", "JSON-LD", "GEOJSON", "JSON SCHEMA"}


def check_link(reader: Reader, report: Report, resource: dict[str, Any]) -> None:
    name = resource.get("name") or resource.get("url")
    url = resource.get("url") or ""
    fmt = (resource.get("format") or "").upper()
    if fmt == "MCP":
        # An MCP endpoint speaks JSON-RPC over POST; a GET is its transport's own answer.
        answer = reader.http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Accept": "application/json, text/event-stream"}, timeout=TIMEOUT)
        ok = answer.status_code in (200, 401)
        report.add(f"link {name}", ok, f"{answer.status_code}")
        return
    answer = reader.get(url)
    if answer.status_code == 401:
        report.add(f"link {name}", True, "401: a restricted endpoint, as EP-64 allows")
        return
    if answer.status_code != 200:
        report.add(f"link {name}", False, f"{answer.status_code} for {url}")
        return
    if fmt in JSON_FORMATS or url.endswith((".json", ".jsonld", ".geojson")):
        try:
            answer.json()
        except ValueError:
            report.add(f"link {name}", False, "200 but not JSON")
            return
    if not answer.content:
        report.add(f"link {name}", False, "200 with an empty body")
        return
    report.add(f"link {name}", True, f"200, {len(answer.content)} bytes")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ckan_url")
    parser.add_argument("datasets", nargs="+")
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument(
        "--max-age",
        action="append",
        default=[],
        metavar="DATASET[:TYPE]=HOURS",
        help="the age a dataset's (or one of its tables') newest timestamp may have: twice its pipeline's period",
    )
    parser.add_argument("--bearer", help="a token for restricted endpoints; read from JC_GATEWAY_TOKEN when absent")
    args = parser.parse_args(argv)

    reader = Reader(args.ckan_url, args.bearer or os.environ.get("JC_GATEWAY_TOKEN"))
    failed = False
    ages: dict[str, float] = {}
    for pair in args.max_age:
        key, _, hours = pair.partition("=")
        ages[key] = float(hours)
    for name in args.datasets:
        tables = {
            key.split(":", 1)[1]: timedelta(hours=hours)
            for key, hours in ages.items()
            if key.startswith(f"{name}:")
        }
        report = check_dataset(
            reader, name, timedelta(hours=ages.get(name, args.max_age_hours)), ages=tables
        )
        print(report.markdown(), end="\n\n")
        failed |= not report.ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

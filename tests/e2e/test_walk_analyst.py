"""An analyst reads a department dataset from a script: NGSI-LD, temporal, CSV, the rate limit (T-5537).

The script half of the analyst walkthrough (`ui/e2e/live/walk-analyst.spec.ts` in the Portal is
the browser half). A person who was refused a dataset, asked for it and got it, reads it the way
a municipality analyst would from Python: one `q` query, every page with `limit`/`offset`, a
temporal query, the `file.csv` download, and as many requests as the edge takes before it
answers 429. Each phase of the walk is one run of this file:

- `WALK_PHASE=before`: no grant yet; every representation refuses (403 with a token, 401 without).
- `WALK_PHASE=granted`: the CSV holds exactly the rows NGSI-LD pages out, the temporal answer
  and the entities carry no attribute `/access` does not grant, and the 429 names Retry-After.
- `WALK_PHASE=revoked`: the grant was removed; the same token is refused again.

Environment (a stage without its variables is skipped, never passed):

- `WALK_ENDPOINT_URL`: `https://<portal>/api/endpoint/<slug>`.
- `WALK_TYPE`: the entity type the grant covers, e.g. `BikeHireDockingStation`.
- `WALK_TOKEN`: the analyst's bearer token, from the run's environment only; never written to
  a file or the report. Without it the walk reads anonymously (a public Endpoint).
- `WALK_PHASE`: `before`, `granted` or `revoked`.
- `WALK_Q` (optional): an NGSI-LD `q` the analyst filters with, e.g. `availableBikeNumber>0`.
- `WALK_RATE_CAP` (optional, default 1500): requests sent at most while looking for the 429.
- `WALK_REPORT` (optional): a JSON file the step table is written to (lap, bytes, seconds,
  requests); it holds no token and no data values.

    WALK_ENDPOINT_URL=... WALK_TYPE=... WALK_PHASE=granted pytest tests/e2e/test_walk_analyst.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import pytest
import requests

TIMEOUT = 120
PAGE = 1000
LD = "application/ld+json"
#: Members of an entity that are not attributes.
CORE = {"id", "type", "@context", "scope", "createdAt", "modifiedAt", "deletedAt"}
STEPS: list[dict[str, Any]] = []


def setting(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} is not set")
    return value


def phase(*wanted: str) -> str:
    current = setting("WALK_PHASE")
    if current not in {"before", "granted", "revoked"}:
        pytest.fail(f"WALK_PHASE must be before, granted or revoked, not {current!r}")
    if current not in wanted:
        pytest.skip(
            f"this case belongs to phase {'/'.join(wanted)}, the run is {current}"
        )
    return current


@pytest.fixture(scope="module")
def walk() -> tuple[str, str, requests.Session]:
    base = setting("WALK_ENDPOINT_URL").rstrip("/")
    entity_type = setting("WALK_TYPE")
    client = requests.Session()
    client.headers["Accept"] = "application/json"
    token = os.environ.get("WALK_TOKEN")
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return base, entity_type, client


@pytest.fixture(scope="module", autouse=True)
def report() -> Any:
    yield
    target = os.environ.get("WALK_REPORT")
    if target and STEPS:
        with open(target, "w", encoding="utf-8") as out:
            json.dump(
                {"phase": os.environ.get("WALK_PHASE"), "steps": STEPS}, out, indent=2
            )


def lap(step: str, started: float, size: int, requests_sent: int, **more: Any) -> None:
    STEPS.append(
        {
            "step": step,
            "seconds": round(time.monotonic() - started, 3),
            "bytes": size,
            "requests": requests_sent,
            **more,
        }
    )


def refused(client: requests.Session) -> int:
    return 403 if "Authorization" in client.headers else 401


def get(client: requests.Session, url: str, **params: Any) -> requests.Response:
    return client.get(url, params=params, timeout=TIMEOUT)


def all_pages(
    base: str, entity_type: str, client: requests.Session, q: str = ""
) -> tuple[list[dict], int, int, int]:
    """Every entity of `entity_type`, page by page; the total the first page names, bytes, requests."""
    entities: list[dict] = []
    size = sent = 0
    total = -1
    while True:
        params: dict[str, Any] = {
            "type": entity_type,
            "limit": PAGE,
            "offset": len(entities),
            "count": "true",
        }
        if q:
            params["q"] = q
        answer = get(client, f"{base}/ngsi-ld/v1/entities", **params)
        sent += 1
        size += len(answer.content)
        assert answer.status_code == 200, (
            f"page at offset {len(entities)}: {answer.status_code} {answer.text[:300]}"
        )
        if total < 0:
            total = int(answer.headers.get("NGSILD-Results-Count", "-1"))
        page = answer.json()
        entities.extend(page)
        if len(page) < PAGE:
            return entities, total, size, sent


def granted_attributes(
    base: str, entity_type: str, client: requests.Session
) -> set[str] | None:
    """What `/access` grants on `entity_type`: a set of attribute names, or None for every one."""
    answer = get(client, f"{base}/access")
    assert answer.status_code == 200, (
        f"/access: {answer.status_code} {answer.text[:300]}"
    )
    names: set[str] = set()
    for permission in answer.json().get("permissions", []):
        if (permission.get("resource") or {}).get("type") not in {
            entity_type,
            "*",
            None,
        }:
            continue
        if "queryEntity" not in permission.get(
            "actions", []
        ) and "queryTemporal" not in permission.get("actions", []):
            continue
        attributes = permission.get("attributes")
        if attributes == "*":
            return None
        names.update(attributes or [])
    assert names, f"/access grants no read of {entity_type}"
    return names


def attributes_of(entities: list[dict]) -> set[str]:
    return {name for entity in entities for name in entity if name not in CORE}


def representations(base: str, entity_type: str) -> dict[str, str]:
    since = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "ngsi-ld entities": f"{base}/ngsi-ld/v1/entities?type={entity_type}&limit=1",
        "ngsi-ld temporal": f"{base}/ngsi-ld/v1/temporal/entities?type={entity_type}&timerel=after&timeAt={since}&lastN=1",
        "csv": f"{base}/file.csv?type={entity_type}",
        "ogc-features": f"{base}/ogc/features/collections",
        "odata": f"{base}/odata/v4/",
        "access": f"{base}/access",
    }


def test_every_representation_refuses_without_the_grant(
    walk: tuple[str, str, requests.Session],
) -> None:
    current = phase("before", "revoked")
    base, entity_type, client = walk
    started = time.monotonic()
    expected = refused(client)
    answers = {}
    for name, url in representations(base, entity_type).items():
        answer = client.get(url, timeout=TIMEOUT)
        # `/access` is the caller's own rights document: a signed-in person reads it with an
        # empty permission list; anonymously it refuses like the rest.
        if name == "access" and answer.status_code == 200:
            assert not answer.json().get("permissions"), (
                f"/access still grants: {answer.text[:300]}"
            )
            answers[name] = 200
            continue
        answers[name] = answer.status_code
    lap(
        f"{current}: every representation refused",
        started,
        0,
        len(answers),
        statuses=answers,
    )
    # A representation the Endpoint does not serve answers 404; none may answer data.
    wrong = {
        name: status
        for name, status in answers.items()
        if name != "access" and status not in {expected, 404}
    }
    assert not wrong, f"expected {expected} (or 404 for one not served), got {wrong}"
    assert answers["ngsi-ld entities"] == expected, answers
    assert answers["csv"] in {expected, 404}, answers


def test_the_csv_holds_the_rows_ngsi_ld_pages_out(
    walk: tuple[str, str, requests.Session],
) -> None:
    phase("granted")
    base, entity_type, client = walk
    started = time.monotonic()
    entities, total, size, sent = all_pages(base, entity_type, client)
    lap("ngsi-ld: every page", started, size, sent, rows=len(entities), counted=total)
    assert total in {-1, len(entities)}, (
        f"NGSILD-Results-Count {total}, pages held {len(entities)}"
    )
    assert len({entity["id"] for entity in entities}) == len(entities), (
        "a page repeated an entity"
    )

    started = time.monotonic()
    answer = get(client, f"{base}/file.csv", type=entity_type)
    assert answer.status_code == 200, (
        f"file.csv: {answer.status_code} {answer.text[:300]}"
    )
    rows = list(csv.DictReader(io.StringIO(answer.content.decode("utf-8-sig"))))
    lap("file.csv", started, len(answer.content), 1, rows=len(rows))
    assert len(rows) == len(entities), (
        f"file.csv has {len(rows)} rows, NGSI-LD paged {len(entities)}"
    )
    assert {row["id"] for row in rows} == {entity["id"] for entity in entities}


def test_a_q_query_and_the_temporal_answer_hold_only_granted_attributes(
    walk: tuple[str, str, requests.Session],
) -> None:
    phase("granted")
    base, entity_type, client = walk
    allowed = granted_attributes(base, entity_type, client)
    q = os.environ.get("WALK_Q", "")

    started = time.monotonic()
    entities, _, size, sent = all_pages(base, entity_type, client, q)
    lap(f"ngsi-ld q={q or '(none)'}", started, size, sent, rows=len(entities))
    if allowed is not None:
        assert attributes_of(entities) <= allowed, (
            f"entities carry {attributes_of(entities) - allowed}"
        )

    started = time.monotonic()
    since = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    answer = get(
        client,
        f"{base}/ngsi-ld/v1/temporal/entities",
        type=entity_type,
        timerel="after",
        timeAt=since,
        lastN=10,
        limit=100,
    )
    assert answer.status_code == 200, (
        f"temporal: {answer.status_code} {answer.text[:300]}"
    )
    history = answer.json()
    lap(
        "ngsi-ld temporal, last day",
        started,
        len(answer.content),
        1,
        entities=len(history),
    )
    assert history, "the temporal query over the last day answered no entity"
    if allowed is not None:
        assert attributes_of(history) <= allowed, (
            f"temporal answer carries {attributes_of(history) - allowed}"
        )


def retry_after_seconds(value: str) -> float:
    if value.strip().isdigit():
        return float(value)
    return (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()


def test_the_rate_limit_answers_429_with_retry_after(
    walk: tuple[str, str, requests.Session],
) -> None:
    phase("granted")
    base, entity_type, client = walk
    cap = int(os.environ.get("WALK_RATE_CAP", "1500"))
    started = time.monotonic()
    for sent in range(1, cap + 1):
        answer = get(client, f"{base}/ngsi-ld/v1/entities", type=entity_type, limit=1)
        if answer.status_code == 429:
            break
        assert answer.status_code == 200, (
            f"request {sent}: {answer.status_code} {answer.text[:200]}"
        )
    else:
        lap("rate limit", started, 0, cap, first_429=None)
        pytest.fail(f"{cap} requests in {time.monotonic() - started:.0f} s and no 429")
    retry = answer.headers.get("Retry-After", "")
    lap("rate limit", started, 0, sent, first_429=sent, retry_after=retry)
    assert retry, f"the 429 after {sent} requests names no Retry-After"
    assert retry_after_seconds(retry) >= 0, f"Retry-After {retry!r} is in the past"

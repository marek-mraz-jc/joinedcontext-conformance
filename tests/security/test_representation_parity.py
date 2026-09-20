from __future__ import annotations

import os
from typing import Any
import pytest
import requests

from representations import (
    Record,
    from_csv,
    from_geojson,
    from_ngsild,
    from_ogc_features,
    from_sta,
    parity_violations,
)


@pytest.fixture(scope="session")
def parity_base_url() -> str:
    val = os.getenv("PARITY_BASE_URL")
    if not val:
        pytest.skip("environment variable PARITY_BASE_URL not set")
    return val.rstrip("/")


@pytest.fixture(scope="session")
def parity_type() -> str:
    return os.getenv("PARITY_TYPE", "AirQualityObserved")


@pytest.fixture(scope="session")
def parity_collection(parity_type: str) -> str:
    return os.getenv("PARITY_COLLECTION", parity_type.lower())


@pytest.fixture(scope="session")
def parity_limit() -> int:
    return int(os.getenv("PARITY_LIMIT", "100"))


@pytest.fixture(scope="session")
def geo_attr() -> str:
    return os.getenv("GEO_ATTR", "location")


@pytest.fixture(scope="session")
def sta_id_field() -> str:
    return os.getenv("STA_ID_FIELD", "@iot.id")


@pytest.fixture(scope="session")
def hidden_attr_set() -> set[str]:
    attr = os.getenv("HIDDEN_ATTR", "internalAuditSecret")
    return {a.strip() for a in attr.split(",") if a.strip()}


@pytest.fixture(scope="session")
def representation_responses(
    parity_base_url: str,
    parity_type: str,
    parity_collection: str,
    parity_limit: int,
    geo_attr: str,
    sta_id_field: str,
) -> dict[str, dict[str, Any]]:
    """Fetch all 5 standardized representation paths."""
    token = os.getenv("TOKEN_VIEWER")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    session = requests.Session()
    paths = {
        "ngsi-ld": (f"{parity_base_url}/ngsi-ld/v1/entities", {"type": parity_type, "limit": parity_limit}),
        "csv": (f"{parity_base_url}/file.csv", {"type": parity_type, "limit": parity_limit}),
        "geojson": (f"{parity_base_url}/file.geojson", {"type": parity_type, "limit": parity_limit}),
        "ogc": (f"{parity_base_url}/ogc/features/collections/{parity_collection}/items", {"limit": parity_limit}),
        "sta": (f"{parity_base_url}/sta/v1.1/Observations", {"$top": parity_limit}),
    }

    results: dict[str, dict[str, Any]] = {}
    for name, (url, params) in paths.items():
        try:
            resp = session.get(url, headers=headers, params=params, timeout=10)
            records: list[Record] = []
            if resp.status_code == 200:
                if name == "ngsi-ld":
                    records = from_ngsild(resp.json())
                elif name == "csv":
                    records = from_csv(resp.text)
                elif name == "geojson":
                    records = from_geojson(resp.json(), geo_attr=geo_attr)
                elif name == "ogc":
                    records = from_ogc_features(resp.json(), geo_attr=geo_attr)
                elif name == "sta":
                    records = from_sta(resp.json(), id_field=sta_id_field)
            results[name] = {
                "status_code": resp.status_code,
                "text": resp.text,
                "records": records,
            }
        except Exception as e:
            results[name] = {
                "status_code": 0,
                "text": str(e),
                "records": [],
            }
    return results


def test_ep06_every_representation_answers_for_the_same_caller(
    representation_responses: dict[str, dict[str, Any]],
):
    """EP-06 — Every representation served under an Endpoint must evaluate the exact same policy decision."""
    statuses = {name: data["status_code"] for name, data in representation_responses.items()}
    auth_errors = {name: code for name, code in statuses.items() if code in (401, 403)}
    successes = {name: code for name, code in statuses.items() if code == 200}

    if auth_errors and successes:
        pytest.fail(
            f"EP-06 policy divergence: caller succeeded on {list(successes.keys())} "
            f"but received auth error on {auth_errors}"
        )

    for name, code in statuses.items():
        if code not in (200, 404):
            pytest.fail(f"EP-06 unexpected status code {code} on representation '{name}'")


def test_ep07_the_same_entities_appear_in_every_representation(
    representation_responses: dict[str, dict[str, Any]],
):
    """EP-07 / R20 — Identical entity set across all enabled representations."""
    active = {
        name: data["records"]
        for name, data in representation_responses.items()
        if data["status_code"] == 200 and name != "sta"
    }
    if len(active) < 2:
        pytest.skip("less than 2 active entity representations available to compare IDs")

    rep_ids = {name: {r.id for r in recs} for name, recs in active.items()}
    rep_names = sorted(rep_ids.keys())
    mismatches = []
    for i, r1 in enumerate(rep_names):
        for r2 in rep_names[i + 1 :]:
            d1 = sorted(rep_ids[r1] - rep_ids[r2])
            d2 = sorted(rep_ids[r2] - rep_ids[r1])
            if d1:
                mismatches.append(f"ids present in '{r1}' but missing in '{r2}': {d1[:5]}")
            if d2:
                mismatches.append(f"ids present in '{r2}' but missing in '{r1}': {d2[:5]}")

    assert not mismatches, "EP-07 entity ID divergence:\n" + "\n".join(mismatches)


def test_ep07_attribute_values_agree_across_representations(
    representation_responses: dict[str, dict[str, Any]],
    hidden_attr_set: set[str],
):
    """EP-07 / TS-03 — Attribute values match under normalization across representations."""
    by_rep = {
        name: data["records"]
        for name, data in representation_responses.items()
        if data["status_code"] == 200
    }
    violations = parity_violations(by_rep, hidden_attrs=hidden_attr_set)
    val_violations = [v for v in violations if "attribute value mismatch" in v]
    assert not val_violations, "EP-07 value mismatches:\n" + "\n".join(val_violations)


def test_ep07_r20_a_hidden_attribute_leaks_through_no_representation(
    representation_responses: dict[str, dict[str, Any]],
    hidden_attr_set: set[str],
):
    """EP-07 / R20 — Hidden attributes never leak through any representation."""
    leaks: list[str] = []
    for name, data in representation_responses.items():
        if data["status_code"] != 200:
            continue
        # Raw response text check
        for h in hidden_attr_set:
            if h in data["text"]:
                leaks.append(f"hidden attribute '{h}' found in raw payload of representation '{name}'")
        # Parsed records check
        for r in data["records"]:
            for attr in r.attrs:
                for h in hidden_attr_set:
                    if attr == h or attr.startswith(f"{h}.") or attr.endswith(f".{h}"):
                        leaks.append(
                            f"hidden attribute '{h}' leaked in parsed record for id '{r.id}' in '{name}'"
                        )
    assert not leaks, "EP-07 leak of forbidden attributes:\n" + "\n".join(sorted(set(leaks)))


def test_ts03_a_representation_that_answers_nothing_is_not_parity(
    representation_responses: dict[str, dict[str, Any]],
):
    """TS-03 vacuity guard: at least two representations must return records."""
    non_empty = {
        name: len(data["records"])
        for name, data in representation_responses.items()
        if data["status_code"] == 200 and len(data["records"]) > 0
    }
    assert len(non_empty) >= 2, (
        f"vacuity failure: fewer than 2 representations returned records. Answered: "
        f"{ {k: (v['status_code'], len(v['records'])) for k, v in representation_responses.items()} }"
    )

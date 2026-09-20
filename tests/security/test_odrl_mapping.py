"""ODRL 2.2 mapping and round-trip conformance suite (R26, R52, EP-57, MIM3-R10)."""

from __future__ import annotations

import json
import os
import pytest
import requests

from access_doc import by_type
from odrl import LEFT_OPERANDS, odrl_violations, round_trip_violations, to_grants


@pytest.fixture
def access_url() -> str:
    url = os.getenv("ACCESS_URL")
    if not url:
        pytest.skip("ACCESS_URL not set")
    return url.rstrip("/")


@pytest.fixture
def token_viewer() -> str | None:
    return os.getenv("TOKEN_VIEWER")


@pytest.fixture
def forbidden_type() -> str:
    return os.getenv("FORBIDDEN_TYPE", "ServiceAccount")


@pytest.fixture
def hidden_attr() -> str:
    return os.getenv("HIDDEN_ATTR", "stationApiKey")


def test_ep57_the_access_surface_answers_odrl_json(access_url: str, token_viewer: str | None):
    headers = {"Accept": "application/odrl+json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    assert res.status_code == 200
    assert "odrl+json" in res.headers.get("Content-Type", "")
    policy = res.json()
    violations = odrl_violations(policy)
    assert not violations, f"ODRL violations: {violations}"


def test_ep57_the_access_surface_answers_turtle(access_url: str, token_viewer: str | None):
    headers = {"Accept": "text/turtle"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    if res.status_code == 406:
        pytest.skip("access surface returned 406 Not Acceptable for text/turtle")
    assert res.status_code == 200

    try:
        import rdflib
    except ImportError:
        pytest.skip("rdflib not installed")

    g = rdflib.Graph()
    g.parse(data=res.text, format="turtle")
    found_permission = any(
        "permission" in str(p).lower() or "permission" in str(o).lower() for s, p, o in g
    )
    assert found_permission, "text/turtle response parsed but contains no odrl:permission triple"


def test_r26_r52_the_odrl_policy_carries_the_same_grants_as_the_json_document(
    access_url: str, token_viewer: str | None
):
    headers_json = {"Accept": "application/json"}
    headers_odrl = {"Accept": "application/odrl+json"}
    if token_viewer:
        headers_json["Authorization"] = f"Bearer {token_viewer}"
        headers_odrl["Authorization"] = f"Bearer {token_viewer}"

    res_json = requests.get(access_url, headers=headers_json, timeout=10)
    assert res_json.status_code == 200
    access_doc = res_json.json()

    res_odrl = requests.get(access_url, headers=headers_odrl, timeout=10)
    assert res_odrl.status_code == 200
    odrl_policy = res_odrl.json()

    violations = round_trip_violations(access_doc, odrl_policy)
    assert not violations, f"round-trip violations: {violations}"


def test_r52_every_left_operand_is_defined_by_the_profile(access_url: str, token_viewer: str | None):
    headers = {"Accept": "application/odrl+json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    assert res.status_code == 200
    odrl_policy = res.json()

    for idx, perm in enumerate(odrl_policy.get("permission", [])):
        for c in perm.get("constraint", []):
            left_op = c.get("leftOperand")
            assert left_op in LEFT_OPERANDS, f"permission[{idx}] uses undefined leftOperand {left_op!r}"
        target = perm.get("target")
        if isinstance(target, dict):
            for r in target.get("refinement", []):
                left_op = r.get("leftOperand")
                assert left_op in LEFT_OPERANDS, f"permission[{idx}].target.refinement uses undefined leftOperand {left_op!r}"


def test_r20_the_odrl_policy_reveals_no_type_the_json_document_hides(
    access_url: str, token_viewer: str | None, forbidden_type: str, hidden_attr: str
):
    headers_json = {"Accept": "application/json"}
    headers_odrl = {"Accept": "application/odrl+json"}
    if token_viewer:
        headers_json["Authorization"] = f"Bearer {token_viewer}"
        headers_odrl["Authorization"] = f"Bearer {token_viewer}"

    res_json = requests.get(access_url, headers=headers_json, timeout=10)
    assert res_json.status_code == 200
    access_doc = res_json.json()

    res_odrl = requests.get(access_url, headers=headers_odrl, timeout=10)
    assert res_odrl.status_code == 200
    odrl_policy = res_odrl.json()

    odrl_str = json.dumps(odrl_policy)
    assert forbidden_type not in odrl_str, f"forbidden type {forbidden_type} leaked in ODRL policy"
    assert hidden_attr not in odrl_str, f"hidden attribute {hidden_attr} leaked in ODRL policy"

    folded = to_grants(odrl_policy)
    assert set(folded.get("types", {})) == set(by_type(access_doc))

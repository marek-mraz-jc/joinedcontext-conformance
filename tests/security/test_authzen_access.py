"""AuthZEN effective permissions discovery suite (T-0086: R51, EP-55, EP-56, EP-59)."""

from __future__ import annotations

import os
import pytest
import requests

from access_doc import (
    by_type,
    check_response_violations,
    consistency_violations,
    disclosure_violations,
    document_violations,
)


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


@pytest.fixture
def access_check_url(access_url: str) -> str:
    return os.getenv("ACCESS_CHECK_URL", f"{access_url}/check")


def test_ep55_ep56_the_access_document_has_the_authzen_shape(access_url: str, token_viewer: str | None):
    headers = {"Accept": "application/json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    assert res.status_code == 200
    assert "application/json" in res.headers.get("Content-Type", "")
    doc = res.json()
    violations = document_violations(doc)
    assert not violations, f"document violations: {violations}"


def test_ep56_the_residual_is_expressed_as_ngsi_ld_query_strings(access_url: str, token_viewer: str | None):
    headers = {"Accept": "application/json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    assert res.status_code == 200
    doc = res.json()
    for t_name, grants in by_type(doc).items():
        res_map = grants["constraints"]
        if "geoQ" in res_map:
            val = res_map["geoQ"]
            assert "georel=" in val and "geometry=" in val, f"{t_name} geoQ missing georel/geometry: {val}"
        if "temporalQ" in res_map:
            val = res_map["temporalQ"]
            assert "timerel=" in val, f"{t_name} temporalQ missing timerel: {val}"
        if "scopeQ" in res_map:
            val = res_map["scopeQ"]
            assert val.startswith("/"), f"{t_name} scopeQ must start with /: {val}"
        if "q" in res_map:
            val = res_map["q"]
            assert len(val) > 0 and not val.startswith("?"), f"{t_name} q invalid: {val}"


def test_ep59_r20_the_document_hides_what_the_caller_may_not_see(
    access_url: str, token_viewer: str | None, forbidden_type: str, hidden_attr: str
):
    headers = {"Accept": "application/json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    assert res.status_code == 200
    doc = res.json()
    violations = disclosure_violations(doc, [forbidden_type], [hidden_attr])
    assert not violations, f"disclosure violations: {violations}"


def test_ep56_a_grant_that_grants_nothing_is_not_a_grant(access_url: str, token_viewer: str | None):
    headers = {"Accept": "application/json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"
    res = requests.get(access_url, headers=headers, timeout=10)
    assert res.status_code == 200
    doc = res.json()
    violations = consistency_violations(doc)
    assert not violations, f"consistency violations: {violations}"


def test_r51_access_check_answers_the_authzen_evaluation_shape(
    access_url: str, access_check_url: str, token_viewer: str | None, forbidden_type: str
):
    headers = {"Content-Type": "application/json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"

    get_res = requests.get(access_url, headers={"Accept": "application/json"}, timeout=10)
    assert get_res.status_code == 200
    granted = by_type(get_res.json())
    allowed_type = next(iter(granted), "AirQualityObserved")
    allowed_ops = sorted(granted.get(allowed_type, {}).get("operations", set()))
    allowed_op = allowed_ops[0] if allowed_ops else "queryEntity"

    req_allowed = {
        "subject": {"id": "did:web:banskabystrica.sk:users:viewer"},
        "action": {"name": allowed_op},
        "resource": {"type": allowed_type},
    }
    res_allowed = requests.post(access_check_url, json=req_allowed, headers=headers, timeout=10)
    assert res_allowed.status_code == 200
    resp_data = res_allowed.json()
    violations = check_response_violations(req_allowed, resp_data)
    assert not violations, f"allowed check response violations: {violations}"
    assert resp_data.get("decision") is True

    req_forbidden = {
        "subject": {"id": "did:web:banskabystrica.sk:users:viewer"},
        "action": {"name": "deleteEntity"},
        "resource": {"type": forbidden_type},
    }
    res_forbidden = requests.post(access_check_url, json=req_forbidden, headers=headers, timeout=10)
    assert res_forbidden.status_code == 200
    resp_data_forb = res_forbidden.json()
    violations = check_response_violations(req_forbidden, resp_data_forb)
    assert not violations, f"forbidden check response violations: {violations}"
    assert resp_data_forb.get("decision") is False


def test_ep55_the_document_matches_what_the_data_surface_actually_does(
    access_url: str, token_viewer: str | None
):
    data_url = os.getenv("DATA_URL")
    if not data_url:
        pytest.skip("DATA_URL not set")

    headers = {"Accept": "application/json"}
    if token_viewer:
        headers["Authorization"] = f"Bearer {token_viewer}"

    granted = by_type(requests.get(access_url, headers=headers, timeout=10).json())
    if not granted:
        pytest.skip("no granted type in the access document")

    target_type = None
    target_info = None
    for t_name, t_info in granted.items():
        if "queryEntity" in t_info["operations"] and (t_info["attributes"] or t_info["full_attributes"]):
            target_type = t_name
            target_info = t_info
            break

    if not target_type or not target_info:
        pytest.skip("no queryEntity type with readable attributes")

    entities_url = f"{data_url.rstrip('/')}/entities"
    res = requests.get(entities_url, params={"type": target_type}, headers=headers, timeout=10)
    if res.status_code == 200 and not target_info["full_attributes"]:
        data = res.json()
        assert isinstance(data, list)
        allowed_attrs = set(target_info["attributes"]) | {"id", "type", "@context"}
        for ent in data:
            ent_keys = set(ent.keys())
            extra_keys = ent_keys - allowed_attrs
            assert not extra_keys, f"entity {ent.get('id')} carries attributes outside its grant: {sorted(extra_keys)}"

    all_known_ops = {"retrieveEntity", "queryEntity", "createEntity", "updateEntity", "appendAttrs", "deleteEntity"}
    forbidden_ops = all_known_ops - target_info["operations"]
    if "deleteEntity" in forbidden_ops:
        del_res = requests.delete(f"{entities_url}/urn:ngsi-ld:{target_type}:conformance-probe", headers=headers, timeout=10)
        assert del_res.status_code in (403, 404, 405), f"unlisted operation deleteEntity returned {del_res.status_code}"

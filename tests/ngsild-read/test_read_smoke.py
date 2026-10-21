"""NGSI-LD read smoke of a deployed Endpoint, anonymous and read-only (T-5044, ETSI GS CIM 009 V1.9.1).

`tests/etsi/smoke.robot` creates its fixture entities, so it runs only on a dev write Endpoint with
the CI service account. This suite reads what the Endpoint already holds and writes nothing, so it
runs on prod as a visitor would. One type is taken from the Endpoint's own type list.

    NGSILD_URL   https://api.{domain}/api/endpoint/{slug}/ngsi-ld/v1
"""

from __future__ import annotations

import os

import pytest
import requests

UA = {
    "User-Agent": "joinedcontext-conformance standards (T-5044)",
    "Accept": "application/json",
}
ERRORS = "https://uri.etsi.org/ngsi-ld/errors/"


@pytest.fixture(scope="module")
def root() -> str:
    url = os.getenv("NGSILD_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip(
            "NGSILD_URL is not set: https://api.{domain}/api/endpoint/{slug}/ngsi-ld/v1"
        )
    return url


def get(url: str, **params) -> requests.Response:
    return requests.get(url, params=params, headers=UA, timeout=60)


@pytest.fixture(scope="module")
def a_type(root: str) -> str:
    """CIM009 6.25.3.1: the type list names what the Endpoint serves."""
    r = get(f"{root}/types")
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body.get("type") == "EntityTypeList", body
    if not body.get("typeList"):
        pytest.skip("the Endpoint serves no type")
    return body["typeList"][0]


@pytest.fixture(scope="module")
def page(root: str, a_type: str) -> list[dict]:
    r = get(f"{root}/entities", type=a_type, limit=5)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_cim009_6_4_3_2_query_by_type_answers_entities_of_that_type(page, a_type):
    assert isinstance(page, list) and page, f"no {a_type} entity on the first page"
    for e in page:
        assert e.get("id", "").startswith("urn:"), e
        assert e.get("type") == a_type, e


def test_cim009_6_5_3_1_retrieve_by_id_answers_the_same_entity(root, page):
    eid = page[0]["id"]
    r = get(f"{root}/entities/{requests.utils.quote(eid, safe='')}")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["id"] == eid


def test_cim009_6_3_2_an_unknown_id_is_resource_not_found_404(root, a_type):
    r = get(f"{root}/entities/urn:ngsi-ld:{a_type}:conformance-never-created")
    assert r.status_code == 404, r.text[:300]
    assert r.json().get("type") == ERRORS + "ResourceNotFound", r.text[:300]


def test_cim009_6_3_13_count_sets_ngsild_results_count(root, a_type):
    r = get(f"{root}/entities", type=a_type, limit=1, count="true")
    assert r.status_code == 200, r.text[:300]
    assert int(r.headers["NGSILD-Results-Count"]) >= 1, r.headers


def test_cim009_6_3_10_a_full_page_links_the_next(root, a_type):
    r = get(f"{root}/entities", type=a_type, limit=1, count="true")
    if int(r.headers.get("NGSILD-Results-Count", "0")) < 2:
        pytest.skip(f"one {a_type} entity: no next page")
    assert 'rel="next"' in r.headers.get("Link", ""), r.headers.get("Link")


def test_cim009_6_3_13_limit_zero_without_count_is_bad_request_data_400(root, a_type):
    r = get(f"{root}/entities", type=a_type, limit=0)
    assert r.status_code == 400, r.text[:300]
    assert r.json().get("type") == ERRORS + "BadRequestData", r.text[:300]


def test_cim009_5_7_2_4_a_query_without_a_selector_is_400(root):
    assert get(f"{root}/entities").status_code == 400


def test_cim009_6_3_7_key_values_holds_plain_values(root, a_type):
    r = get(f"{root}/entities", type=a_type, limit=1, format="keyValues")
    assert r.status_code == 200, r.text[:300]
    entity = r.json()[0]
    members = {k: v for k, v in entity.items() if k not in ("id", "type", "@context")}
    typed = [
        k
        for k, v in members.items()
        if isinstance(v, dict)
        and v.get("type") in ("Property", "Relationship", "GeoProperty")
    ]
    assert not typed, f"keyValues still wraps {typed}"


def test_cim009_6_3_15_geojson_answers_a_feature_collection(root, a_type):
    r = requests.get(
        f"{root}/entities",
        params={"type": a_type, "limit": 2},
        headers={**UA, "Accept": "application/geo+json"},
        timeout=60,
    )
    assert r.status_code == 200, r.text[:300]
    assert r.json().get("type") == "FeatureCollection", r.text[:300]

"""OGC query parameters and the CQL2 subset (T-0058, EP-29, EP-30, EP-33…EP-36, EP-39).

Each test is named after the requirement it holds the endpoint to, and each one can fail: the
filters are derived from a feature that is really there, then asserted to exclude it and to
include it again.
"""

from __future__ import annotations

import pytest

# docs/Testing/02-conformance-tests.md §2: the classes the platform claims, and only those
CLAIMED = {
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
    "http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/crs",
    "http://www.opengis.net/spec/cql2/1.0/conf/basic-cql2",
    "http://www.opengis.net/spec/cql2/1.0/conf/cql2-text",
    "http://www.opengis.net/spec/cql2/1.0/conf/cql2-json",
    "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/filter",
    "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/features-filter",
}
# never claimed: HTML and Part 4 write access (EP-39)
NEVER_CLAIMED = ("conf/html", "ogcapi-features-4")


def bbox_of(feature: dict) -> tuple[float, float]:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    assert coordinates, f"feature {feature.get('id')} carries no geometry to filter on"
    while isinstance(coordinates[0], list):
        coordinates = coordinates[0]
    return float(coordinates[0]), float(coordinates[1])


def test_ep29_landing_page_offers_the_core_resource_hierarchy(get):
    """EP-29 — OGC API Features MUST be served conforming to the standard Part 1 Core resource hierarchy."""
    response = get("")
    assert response.status_code == 200
    relations = {link.get("rel") for link in response.json().get("links", [])}
    for required in ("self", "conformance", "data"):
        assert required in relations, f"the landing page has no {required} link: {sorted(relations)}"


def test_ep30_conformance_declares_exactly_the_claimed_classes(get):
    """EP-30 — Conformance classes claimed MUST be exactly Part 1 Core, OpenAPI 3.0, GeoJSON, Part 2 CRS, and basic CQL2 filtering."""
    response = get("/conformance")
    assert response.status_code == 200
    declared = set(response.json().get("conformsTo", []))
    assert declared, "the endpoint declares no conformance class at all"
    assert declared == CLAIMED, (
        f"claimed but not declared: {sorted(CLAIMED - declared)}; "
        f"declared but not claimed: {sorted(declared - CLAIMED)}"
    )
    for forbidden in NEVER_CLAIMED:
        assert not any(forbidden in item for item in declared), f"{forbidden} must not be declared"


def test_ep34_bbox_excludes_a_feature_outside_it(get, collection, features):
    """EP-34 — OGC query parameters (bbox, datetime, limit, filter) MUST map directly to NGSI-LD query AST constraints and intersect with caller grants."""
    feature = features[0]
    longitude, latitude = bbox_of(feature)

    far_away = f"{longitude + 40},{latitude + 30},{longitude + 41},{latitude + 31}"
    excluded = get(f"/collections/{collection}/items", bbox=far_away)
    assert excluded.status_code == 200
    assert feature["id"] not in [f["id"] for f in excluded.json()["features"]], (
        "a feature outside the requested bounding box came back"
    )

    around = f"{longitude - 0.01},{latitude - 0.01},{longitude + 0.01},{latitude + 0.01}"
    included = get(f"/collections/{collection}/items", bbox=around)
    assert included.status_code == 200
    assert feature["id"] in [f["id"] for f in included.json()["features"]], (
        "a bounding box drawn around the feature did not return it, so the filter is not a filter"
    )


def test_ep34_malformed_bbox_is_refused(get, collection):
    """EP-34 — OGC query parameters (bbox, datetime, limit, filter) MUST map directly to NGSI-LD query AST constraints and intersect with caller grants."""
    for malformed in ["1,2,3", "a,b,c,d", "10,10,0,0,0,0,0"]:
        response = get(f"/collections/{collection}/items", bbox=malformed)
        assert response.status_code == 400, f"bbox={malformed!r} answered {response.status_code}"


def test_ep34_datetime_window_excludes_a_feature_outside_it(get, collection, features):
    """EP-34 — OGC query parameters (bbox, datetime, limit, filter) MUST map directly to NGSI-LD query AST constraints and intersect with caller grants."""
    timestamps = [
        (f["id"], value)
        for f in features
        for key, value in (f.get("properties") or {}).items()
        if key in ("observedAt", "datetime", "resultTime", "phenomenonTime") and isinstance(value, str)
    ]
    if not timestamps:
        pytest.skip("no temporal property on the sampled features")
    feature_id, moment = timestamps[0]

    excluded = get(f"/collections/{collection}/items", datetime="1970-01-01T00:00:00Z/1970-01-02T00:00:00Z")
    assert excluded.status_code == 200
    assert feature_id not in [f["id"] for f in excluded.json()["features"]]

    included = get(f"/collections/{collection}/items", datetime=f"{moment}/{moment}")
    assert included.status_code == 200
    assert feature_id in [f["id"] for f in included.json()["features"]], (
        f"the exact instant {moment} of the feature did not return it"
    )


def test_ep35_cql2_comparison_narrows_the_result(get, collection, features):
    """EP-35 — The supported CQL2 subset MUST cover basic comparison, logical, temporal, and spatial operators, rejecting unsupported constructs with HTTP 400."""
    feature = features[0]
    candidates = [
        (key, value)
        for key, value in (feature.get("properties") or {}).items()
        if isinstance(value, (int, float, str)) and not isinstance(value, bool)
    ]
    if not candidates:
        pytest.skip("the sampled feature has no scalar property to filter on")
    name, value = candidates[0]
    literal = f"'{value}'" if isinstance(value, str) else str(value)

    matching = get(f"/collections/{collection}/items", filter=f"{name} = {literal}", **{"filter-lang": "cql2-text"})
    assert matching.status_code == 200
    assert feature["id"] in [f["id"] for f in matching.json()["features"]]

    excluding = get(f"/collections/{collection}/items", filter=f"{name} <> {literal}", **{"filter-lang": "cql2-text"})
    assert excluding.status_code == 200
    assert feature["id"] not in [f["id"] for f in excluding.json()["features"]], (
        "the negation of a matching filter still returned the feature"
    )


def test_ep35_unsupported_cql2_construct_is_refused(get, collection):
    """EP-35 — The supported CQL2 subset MUST cover basic comparison, logical, temporal, and spatial operators, rejecting unsupported constructs with HTTP 400."""
    for expression in ["S_CROSSES(geometry,POINT(1 1))", "casei(name) LIKE 'x%'", "name = "]:
        response = get(
            f"/collections/{collection}/items", filter=expression, **{"filter-lang": "cql2-text"}
        )
        assert response.status_code == 400, (
            f"filter={expression!r} answered {response.status_code}; an unsupported construct is a 400"
        )


def test_ep36_pagination_uses_limit_and_a_next_link_without_repeats(get, http_session, collection):
    """EP-36 — Feature pagination MUST use limit and an opaque next link encoding the NGSI-LD offset, reporting total matches via post-filtered counts."""
    first = get(f"/collections/{collection}/items", limit=1)
    assert first.status_code == 200
    body = first.json()
    assert len(body["features"]) <= 1, "limit=1 returned more than one feature"

    next_link = next((link["href"] for link in body.get("links", []) if link.get("rel") == "next"), None)
    if not next_link:
        pytest.skip("the collection fits in one page, so there is no next link to follow")

    second = http_session.get(next_link, timeout=60)
    assert second.status_code == 200, f"the next link answered {second.status_code}"
    first_ids = [f["id"] for f in body["features"]]
    second_ids = [f["id"] for f in second.json()["features"]]
    assert not set(first_ids) & set(second_ids), (
        f"the next page repeats {sorted(set(first_ids) & set(second_ids))}, so the offset is not advancing"
    )


def test_ep33_an_unknown_feature_is_404(get, collection):
    """EP-33 — Feature identifiers MUST equal the entity URN, returning HTTP 404 for nonexistent or forbidden resources."""
    response = get(f"/collections/{collection}/items/urn:ngsi-ld:Conformance:does-not-exist-9f3a")
    assert response.status_code == 404, f"an unknown feature answered {response.status_code}"


def test_ep39_write_methods_are_rejected(http_session, landing_url, collection):
    """EP-39 — Write operations through the OGC Features representation MUST NOT exist, returning HTTP 405 Method Not Allowed on all non-safe methods."""
    url = f"{landing_url}/collections/{collection}/items"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = http_session.request(method, url, json={"type": "Feature"}, timeout=60)
        assert response.status_code == 405, f"{method} answered {response.status_code}, expected 405"

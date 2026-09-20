import typing
import requests
from conftest import entity_ids


def test_r20_forbidden_entity_get_returns_404_byte_identical_to_nonexistent(
    get: typing.Callable[..., requests.Response],
    forbidden_entity_id: str,
):
    """R20 — Query results are silently narrowed to the permitted subset. Explicit single-entity retrieval (GET /entities/{id}) of a forbidden entity MUST return 404 (not 403) to avoid existence disclosure."""
    forbidden_res = get(f"/entities/{forbidden_entity_id}")
    assert forbidden_res.status_code == 404

    prefix = forbidden_entity_id.rsplit(":", 1)[0]
    random_id = f"{prefix}:conformance-nonexistent-7f8a9b"
    nonexistent_res = get(f"/entities/{random_id}")
    assert nonexistent_res.status_code == 404
    assert forbidden_res.content == nonexistent_res.content


def test_r20_404_is_rfc7807_resource_not_found(
    get: typing.Callable[..., requests.Response],
    forbidden_entity_id: str,
):
    """R20 — Query results are silently narrowed to the permitted subset. Explicit single-entity retrieval (GET /entities/{id}) of a forbidden entity MUST return 404 (not 403) to avoid existence disclosure."""
    res = get(f"/entities/{forbidden_entity_id}")
    assert res.status_code == 404
    body = res.json()
    assert body.get("type") == "https://uri.etsi.org/ngsi-ld/errors/ResourceNotFound"


def test_r20_collection_query_silently_narrows_forbidden_entities(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    granted_type: str,
    forbidden_entity_id: str,
):
    """R20 — Query results are silently narrowed to the permitted subset. Explicit single-entity retrieval (GET /entities/{id}) of a forbidden entity MUST return 404 (not 403) to avoid existence disclosure."""
    res = get("/entities", token=token_viewer, type=granted_type, limit=1000)
    assert res.status_code == 200
    ids = entity_ids(res)
    assert forbidden_entity_id not in ids, "a forbidden entity came back through the collection query"


def test_gw12_empty_intersection_is_200_with_empty_array(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    granted_type: str,
):
    """GW12 — Empty intersection on a read MUST yield HTTP 200 with an empty result (silent narrowing), with the opt-in NGSILD-Results-Restricted: true header. Empty intersection on a write MUST yield DENY."""
    res = get("/entities", token=token_viewer, type=granted_type, q='id=="urn:ngsi-ld:conformance:nonexistent-match-999"')
    assert res.status_code == 200
    assert res.json() == []


def test_r22_results_restricted_header_opt_in(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    granted_type: str,
    hidden_attr: str,
):
    """R22 — A caller SHOULD be able to distinguish "empty result" from "narrowed result" via an opt-in response header (NGSILD-Results-Restricted: true)."""
    # a request that is certainly narrowed: it asks for an attribute the grant does not cover
    res_opt_in = get(
        "/entities",
        token=token_viewer,
        type=granted_type,
        attrs=hidden_attr,
        headers={"NGSILD-Results-Restricted": "true"},
    )
    assert res_opt_in.status_code == 200
    assert res_opt_in.headers.get("NGSILD-Results-Restricted", "").lower() == "true", (
        "a narrowed result did not carry the opt-in header; headers were "
        f"{sorted(res_opt_in.headers)}"
    )

    res_no_opt_in = get("/entities", token=token_viewer, type=granted_type, attrs=hidden_attr)
    assert res_no_opt_in.status_code == 200
    assert "NGSILD-Results-Restricted" not in res_no_opt_in.headers


def test_gw12_write_forbidden_entity_denied_with_403(
    http_session: requests.Session,
    space_url: str,
    forbidden_entity_id: str,
):
    """GW12 — Empty intersection on a read MUST yield HTTP 200 with an empty result (silent narrowing), with the opt-in NGSILD-Results-Restricted: true header. Empty intersection on a write MUST yield DENY."""
    payload = {
        "conformanceAttr": {
            "type": "Property",
            "value": "illegal-mutation",
        }
    }
    url = f"{space_url}/entities/{forbidden_entity_id}/attrs"
    res = http_session.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
    assert res.status_code == 403, (
        f"an anonymous write to a forbidden entity answered {res.status_code}; "
        "a read narrows, a write denies (GW12)"
    )
    assert "application/json" in res.headers.get("Content-Type", "")
    body = res.json()
    assert body.get("type", "").startswith("https://uri.etsi.org/ngsi-ld/errors/"), body
    assert "title" in body and "detail" in body, body

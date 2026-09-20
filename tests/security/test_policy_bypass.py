import typing
import pytest
import requests
from conftest import entity_ids


def test_gw10_query_q_outside_grant_preserves_projection(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
    hidden_attr: str,
):
    """GW10 — Effective request = userRequest ∧ (grant₁ ∨ grant₂ ∨ … ∨ grantₙ). AND inside one policy's constraint set, OR across policies, applied on the parsed AST — never by naive query-parameter concatenation, so cross-policy privilege bleed is impossible."""
    res = get("/entities", type=granted_type, q=f'{hidden_attr}=="sensor-secret"')
    assert res.status_code == 200
    entities = res.json()
    if not entities:
        # An empty answer is what GW12 asks for when the intersection is empty: a filter on an
        # attribute outside the grant matches nothing the caller may see. There is then no
        # entity to inspect, so the projection cannot be observed here rather than being broken.
        pytest.skip("the filter on the hidden attribute intersects nothing this caller may read")
    for entity in entities:
        assert hidden_attr not in entity, f"{hidden_attr} leaked through a q filter on it: {entity}"


def test_r14_caller_q_cannot_widen_grant(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    granted_type: str,
    forbidden_entity_id: str,
):
    """R14 — Rewriting MUST be transparent: the caller sends a plain NGSI-LD request with no security parameters; the caller MAY add own filters; these are AND-ed with the injected ones."""
    res = get(
        "/entities",
        token=token_viewer,
        type=granted_type,
        q=f'id=="{forbidden_entity_id}"|id~=".*"',
    )
    assert res.status_code == 200
    ids = entity_ids(res)
    assert forbidden_entity_id not in ids


def test_r13_scopeq_traversal_and_regex_injection_cannot_escape(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
):
    """R13 — Scope constraints MUST be folded into the q expression as anchored regex on the scope attribute (scope~="^/geo/…(/.*)?$") and the merged query MUST be sent via POST /ngsi-ld/v1/entityOperations/query, preserving pagination and avoiding HTTP 414."""
    baseline = get("/entities", type=granted_type)
    assert baseline.status_code == 200
    permitted = set(entity_ids(baseline))

    for probe in ["/geo/x/../y", ".*", "^/geo/.*$", "/#", "/geo/banskabystrica/../../platform"]:
        res = get("/entities", type=granted_type, scopeQ=probe)
        assert res.status_code in (200, 400), f"scopeQ={probe!r} produced {res.status_code}"
        if res.status_code == 200:
            widened = set(entity_ids(res)) - permitted
            assert not widened, f"scopeQ={probe!r} escaped the grant and returned {sorted(widened)}"


def test_gw11_type_set_intersection_forbidden_type_is_empty(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    forbidden_type: str,
):
    """GW11 — Per-dimension intersection: tenant pinned by the gateway, client value ignored; type set intersection, a requested type outside every grant ⇒ empty read / denied write."""
    res = get("/entities", token=token_viewer, type=forbidden_type)
    assert res.status_code == 200
    assert res.json() == []


def test_gw10_attrs_naming_hidden_attr_never_returned(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
    hidden_attr: str,
):
    """GW10 — Effective request = userRequest ∧ (grant₁ ∨ grant₂ ∨ … ∨ grantₙ). AND inside one policy's constraint set, OR across policies, applied on the parsed AST — never by naive query-parameter concatenation, so cross-policy privilege bleed is impossible."""
    res = get("/entities", type=granted_type, attrs=hidden_attr)
    assert res.status_code == 200
    entities = res.json()
    if not entities:
        # Same as above: `attrs` naming only an attribute outside the grant is an empty
        # intersection, and GW12 answers it with 200 and an empty result.
        pytest.skip("attrs names only an attribute outside the grant, so the intersection is empty")
    for entity in entities:
        assert hidden_attr not in entity, f"attrs={hidden_attr} was honoured outside the grant: {entity}"


def test_r13_post_query_parity_with_get(
    get: typing.Callable[..., requests.Response],
    post_query: typing.Callable[..., requests.Response],
    token_viewer: str,
    granted_type: str,
    hidden_attr: str,
    forbidden_entity_id: str,
):
    """R13 — Scope constraints MUST be folded into the q expression as anchored regex on the scope attribute (scope~="^/geo/…(/.*)?$") and the merged query MUST be sent via POST /ngsi-ld/v1/entityOperations/query, preserving pagination and avoiding HTTP 414."""
    get_res = get("/entities", token=token_viewer, type=granted_type, attrs=hidden_attr)
    # CIM 009 clause 5.2.23: the body is a Query object, entities is an EntitySelector array
    post_res = post_query(
        {"type": "Query", "entities": [{"type": granted_type}], "attrs": [hidden_attr]},
        token=token_viewer,
    )

    assert get_res.status_code == 200
    assert post_res.status_code == 200
    assert entity_ids(get_res) == entity_ids(post_res)
    assert forbidden_entity_id not in entity_ids(post_res)
    for entity in post_res.json():
        assert hidden_attr not in entity


def test_r12_oversized_query_enforces_policy_without_leak(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    granted_type: str,
    forbidden_entity_id: str,
):
    """R12 — When multiple independent policies apply, they MUST be combined per policy as (policy-constraints AND …) joined with OR across policies — never by naive parameter concatenation. Cross-product privilege bleed between scopeQ of one policy and q of another MUST be impossible."""
    padding = "a" * 1500
    large_q = f'id!="conformance-{padding}";id~=".*"'
    res = get("/entities", token=token_viewer, type=granted_type, q=large_q)
    assert res.status_code in (200, 414)
    if res.status_code == 200:
        ids = entity_ids(res)
        assert forbidden_entity_id not in ids

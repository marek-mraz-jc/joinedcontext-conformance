import typing
import requests
from conftest import entity_ids


def test_gw20_forged_tenant_header_ignored(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
):
    """GW20 — The NGSILD-Tenant header MUST be stripped from client input and injected by the gateway from the caller's grant. A caller never chooses a tenant; the tenant is a conclusion of authentication."""
    baseline = get("/entities", type=granted_type)
    forged = get("/entities", type=granted_type, headers={"NGSILD-Tenant": "foreign-tenant"})

    assert baseline.status_code == 200
    assert forged.status_code == 200
    assert entity_ids(baseline) == entity_ids(forged)


def test_gw20_forged_tenant_header_casings_ignored(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
):
    """GW20 — The NGSILD-Tenant header MUST be stripped from client input and injected by the gateway from the caller's grant. A caller never chooses a tenant; the tenant is a conclusion of authentication."""
    baseline = get("/entities", type=granted_type)
    assert baseline.status_code == 200
    expected_ids = entity_ids(baseline)

    for casing in ["ngsild-tenant", "NGSILD-Tenant", "NGSILD-TENANT"]:
        res = get("/entities", type=granted_type, headers={casing: "foreign-tenant"})
        assert res.status_code == 200
        assert entity_ids(res) == expected_ids

    res_param = get("/entities", type=granted_type, params={"NGSILD-Tenant": "foreign-tenant"})
    assert res_param.status_code == 200
    assert entity_ids(res_param) == expected_ids


def test_gw25_identity_constraint_headers_rejected_or_ignored(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
):
    """GW25 — Identity/constraint headers MUST be trusted only on the internal listener; the public listener MUST reject them."""
    baseline = get("/entities", type=granted_type)
    assert baseline.status_code == 200
    expected_ids = entity_ids(baseline)

    forged_headers = {
        "X-Userinfo": "roles=admin;tenant=platform",
        "X-Allowed-Scope-Ids": "*",
        "X-Forwarded-User": "admin@joinedcontext.com",
    }
    res = get("/entities", type=granted_type, headers=forged_headers)
    assert res.status_code in (200, 400, 403)
    if res.status_code == 200:
        assert entity_ids(res) == expected_ids


def test_sp07_nonexistent_forged_tenant_not_producing_broker_error(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
):
    """SP-07 — The gateway MUST strip any inbound NGSILD-Tenant header, strip the /cs/{space} prefix, and inject NGSILD-Tenant: {space} on the internal hop only."""
    res = get("/entities", type=granted_type, headers={"NGSILD-Tenant": "nonexistent-tenant-xyz-404"})
    assert res.status_code == 200
    assert "NonexistentTenant" not in res.text


def test_sp06_token_space_mismatch_returns_404_matching_nonexistent_space(
    get: typing.Callable[..., requests.Response],
    token_viewer: str,
    other_space_url: str,
):
    """SP-06 — The gateway MUST derive the tenant from the /cs/{space} path segment for anonymous access and from the token claim for authenticated access. A path/token tenant mismatch MUST return the same 404 as a nonexistent space (no existence disclosure)."""
    mismatch_res = get("/entities", token=token_viewer, base_url=other_space_url)
    assert mismatch_res.status_code == 404

    space_segment = other_space_url.split("/cs/")[1].split("/")[0]
    nonexistent_space_url = other_space_url.replace(
        f"/cs/{space_segment}", "/cs/nonexistent-space-token-probe", 1
    )
    nonexistent_res = get("/entities", token=token_viewer, base_url=nonexistent_space_url)
    assert nonexistent_res.status_code == 404
    assert mismatch_res.content == nonexistent_res.content


def test_sp05_response_never_echoes_tenant_header(
    get: typing.Callable[..., requests.Response],
    granted_type: str,
):
    """SP-05 — The public API contract MUST NOT contain the NGSILD-Tenant header."""
    res = get("/entities", type=granted_type)
    assert res.status_code == 200
    for header in res.headers:
        assert header.lower() != "ngsild-tenant"

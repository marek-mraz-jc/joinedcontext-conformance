import os
import typing

import pytest
import requests

from conftest import entity_ids
from test_tenant_matrix import INPUTS, ROWS, TIMEOUT, Row, _fill, _masked, _names, _url


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


# -- T-5566 (GW12, AG-38, GW25): the headers the platform's own services write ---------------
#
# The gateway tells the broker who the caller is in `x-jc-*` headers, stamps provenance in
# `jc-provenance-*`, and decides an MQTT CONNECT on Linkerd's `l5d-client-id`. The edge clears
# all three families by prefix and the gateway strips the first two again (`tenancy.rs`
# `strip_client_headers`), so a caller who writes them from outside changes nothing: each door
# answers project A's token on project B's names exactly as it does without them. The doors and
# the inputs are `test_tenant_matrix.py`'s, so this runs wherever the matrix runs.

#: Each value claims the most a header could: the platform's admins, the gateway's own client
#: (whose hops the broker trusts), project B's groups, and a broker's mesh identity.
FORGED_IDENTITY = {
    "x-jc-subject": "service-account-context-gateway",
    "x-jc-groups": "/platform-admins,/{project}/stewards",
    "x-jc-roles": "admin,steward",
    "x-jc-client": "context-gateway",
    "x-jc-restricted": "false",
    "jc-provenance-actor": "user:admin",
    "l5d-client-id": "context-gateway.jc-context-gateway.serviceaccount.identity.linkerd.cluster.local",
}

#: The space, Endpoint, WebSocket, OGC and MCP doors; reads only, so nothing is written to B.
DOORS = ("ngsi-ld", "space", "websocket", "ogc", "mcp-endpoint", "mcp-space", "mcp-hub", "mcp-named")
SPOOF_ROWS = tuple(
    row for row in ROWS if row.surface in DOORS and row.method in ("GET", "POST") and row.verb != "write"
    and row.kind != "absent" and row.host == "gateway"
)


@pytest.fixture(scope="session")
def doors() -> dict[str, str]:
    """`test_tenant_matrix.py`'s inputs; skips without them, as the matrix does."""
    missing = [name for name in INPUTS if not os.getenv(name)]
    if missing:
        pytest.skip(f"environment variables not set: {', '.join(missing)}")
    return {name: os.environ[name] for name in INPUTS}


def _door(row: Row, inputs: dict[str, str], names: dict[str, str], extra: dict[str, str]) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {inputs['TOKEN_PROJECT_A']}", **_fill(extra, names)}
    if row.kind == "stream":
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect

        ws_url = "ws" + _url(row, inputs, names).removeprefix("http")
        try:
            with connect(ws_url, additional_headers=headers, open_timeout=TIMEOUT) as ws:
                try:
                    frame = ws.recv(timeout=2)
                except Exception:  # noqa: BLE001 - a quiet or closed stream carries nothing
                    frame = ""
                return 101, _masked(frame if isinstance(frame, str) else frame.decode(errors="replace"), names)
        except InvalidStatus as refused:
            return refused.response.status_code, _masked(refused.response.body.decode(errors="replace"), names)
    response = requests.request(
        row.method,
        _url(row, inputs, names),
        headers={**headers, "Accept": "application/json"},
        json=_fill(row.body, names),
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    return response.status_code, _masked(response.text, names)


@pytest.mark.parametrize("row", SPOOF_ROWS, ids=lambda row: row.id)
def test_platform_identity_headers_from_outside_change_nothing(row: Row, doors: dict[str, str]):
    """GW12, AG-38, GW25 — no identity, provenance or mesh-identity header a service trusts can be
    set from outside the cluster: B's door answers A the same with and without them."""
    names = _names(doors, other=True)
    plain = _door(row, doors, names, {})
    forged = _door(row, doors, names, FORGED_IDENTITY)
    assert forged == plain, f"{row.id}: forged identity headers changed the answer from {plain} to {forged}"

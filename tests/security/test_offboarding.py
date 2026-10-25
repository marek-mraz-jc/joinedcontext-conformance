"""T-5568, TR-03187 AR-6: a person disabled in the Portal is refused on every surface within
15 s, while the token they hold is still unexpired.

Run against a throwaway stack only: the walk disables a person and enables them again.

| Variable | What |
|---|---|
| `OFFBOARD_PORTAL_URL` | the Portal host, e.g. `https://portal.{domain}` |
| `OFFBOARD_GATEWAY_URL` | the gateway's public base, e.g. `https://{domain}` |
| `OFFBOARD_ADMIN_TOKEN` | an Organization Administrator's bearer for the Portal |
| `OFFBOARD_PERSON_ID` | the Keycloak user id of a throwaway person (never the administrator) |
| `OFFBOARD_PERSON_TOKEN` | that person's access token, audiences `portal-api`, `context-gateway` and the Endpoint's slug, at least 60 s from expiry |
| `OFFBOARD_ENDPOINT` | the slug of an Endpoint that grants the person a read and serves MCP |
"""

import os
import time

import pytest
import requests

WITHIN = 15.0


def _env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"environment variable {name} not set")
    return value


def _surfaces(portal: str, gateway: str, slug: str) -> dict[str, tuple[str, str, dict | None]]:
    tools = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    return {
        "portal": ("GET", f"{portal}/api/v1/auth/me", None),
        "gateway REST": (
            "GET",
            f"{gateway}/api/endpoint/{slug}/ngsi-ld/v1/entities?limit=1&type=*",
            None,
        ),
        "hub MCP": ("POST", f"{gateway}/api/mcp", tools),
        "endpoint MCP": ("POST", f"{gateway}/api/endpoint/{slug}/mcp", tools),
    }


def _status(call: tuple[str, str, dict | None], token: str) -> int:
    verb, url, body = call
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    return requests.request(verb, url, json=body, headers=headers, timeout=10).status_code


def test_a_disabled_person_is_refused_on_every_surface_within_fifteen_seconds():
    portal = _env("OFFBOARD_PORTAL_URL").rstrip("/")
    gateway = _env("OFFBOARD_GATEWAY_URL").rstrip("/")
    admin = _env("OFFBOARD_ADMIN_TOKEN")
    person = _env("OFFBOARD_PERSON_ID")
    token = _env("OFFBOARD_PERSON_TOKEN")
    surfaces = _surfaces(portal, gateway, _env("OFFBOARD_ENDPOINT"))
    people = f"{portal}/api/v1/organization/people/{person}"
    admin_headers = {"Authorization": f"Bearer {admin}"}

    before = {name: _status(call, token) for name, call in surfaces.items()}
    assert all(status != 401 for status in before.values()), before

    disabled = requests.post(f"{people}/disable", headers=admin_headers, timeout=10)
    assert disabled.status_code == 200, disabled.text
    try:
        deadline = time.monotonic() + WITHIN
        open_doors = dict(surfaces)
        while open_doors and time.monotonic() < deadline:
            open_doors = {n: c for n, c in open_doors.items() if _status(c, token) != 401}
            if open_doors:
                time.sleep(1)
        assert not open_doors, f"still admitted after {WITHIN} s: {sorted(open_doors)}"
    finally:
        enabled = requests.post(f"{people}/enable", headers=admin_headers, timeout=10)
        assert enabled.status_code == 200, enabled.text

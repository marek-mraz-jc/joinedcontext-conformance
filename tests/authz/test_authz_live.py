"""The Portal's authorization matrix, cell by cell, against a running Portal (T-2797, PF-52, AC-01).

The table is the Portal's `tests/authz_matrix.yaml`, which the Portal's own tests hold on every
push against a mocked forge. Here each cell dev can answer is sent for real, with the token of a
demo person of that role (`. tests/dev.env.sh authz`):

- a read is sent for every role that has a token;
- a write only where the row says `live: true`: a resource proposal, sent with `?dryRun=All`,
  or a call on an object that does not exist, so a check that came loose writes nothing;
- a refusal cell must answer that status exactly; an `allow` cell must not answer 401 or 403
  (dev's objects are not the fixture's, so a 404 there can be a missing object);
- a 422 or 503 cell is the fixture's (a body or a service it lacks), and is not measured here.

The internal listener and `/metrics` are not reachable from outside by design and are not sent.
"""

from __future__ import annotations

import os

import pytest
import requests

from conftest import load_matrix

TABLE = load_matrix()
# Who stands for each role on dev; a role without a token is not measured.
TOKENS = {
    "anonymous": "",
    "viewer": os.environ.get("AUTHZ_TOKEN_VIEWER"),
    "editor": os.environ.get("AUTHZ_TOKEN_EDITOR"),
    "steward": os.environ.get("AUTHZ_TOKEN_STEWARD"),
    "approver": os.environ.get("AUTHZ_TOKEN_APPROVER"),
    "org-admin": os.environ.get("AUTHZ_TOKEN_ORG_ADMIN"),
    "other-member": os.environ.get("AUTHZ_TOKEN_OTHER_MEMBER"),
    "service-account": os.environ.get("AUTHZ_TOKEN_SERVICE_ACCOUNT"),
}
NOT_MEASURED = {"422", "503", "415"}
PLACEHOLDERS = {
    "{project}": os.environ.get("AUTHZ_PROJECT", "helsinki"), "{plural}": "endpoints",
    "{name}": "authz-probe-missing", "{id}": "0000000000000000", "{kind}": "Endpoint",
    "{space}": os.environ.get("AUTHZ_PROJECT", "helsinki"), "{asset}": "logo", "{fn}": "summary",
    "{style}": "streets", "{z}": "1", "{x}": "1", "{tile}": "1.png", "{keyId}": "k1",
    "{*path}": "index.js", "{component}": "proxy", "{run}": "r1",
}


def url(base: str, path: str) -> str:
    for placeholder, value in PLACEHOLDERS.items():
        path = path.replace(placeholder, value)
    if path.startswith(("/apps/", "/.well-known/")):
        return base + path
    return base + "/api/v1" + path


def cells():
    roles = TABLE.get("roles", [])
    for row in TABLE.get("routes", []):
        method, path = row["route"].split(" ", 1)
        if path.startswith("/internal/") or path == "/metrics":
            continue
        if method != "GET" and not row.get("live"):
            continue
        for role, expect in zip(roles, row["expect"]):
            yield pytest.param(row, method, path, role, str(expect), id=f"{row['route']} as {role}")


def test_the_table_is_there():
    assert TABLE["routes"], f"no authorization matrix at JC_AUTHZ_MATRIX or the Portal's clone"


@pytest.mark.parametrize("row,method,path,role,expect", list(cells()))
def test_cell(portal_url, row, method, path, role, expect):
    token = TOKENS[role]
    if token is None:
        pytest.skip(f"no token for {role} on this instance (AUTHZ_TOKEN_{role.upper().replace('-', '_')})")
    if expect in NOT_MEASURED:
        pytest.skip(f"{expect} is the fixture's answer, not a rule dev can show")
    target = url(portal_url, path)
    if "{plural}" in path and method in ("POST", "PUT", "PATCH"):
        target += "?dryRun=All"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = row.get("body", {}) if method in ("POST", "PUT", "PATCH") else None
    answer = requests.request(method, target, headers=headers, json=body, timeout=30, allow_redirects=False)
    if expect == "allow":
        assert answer.status_code not in (401, 403), (
            f"{row['route']} refused {role} with {answer.status_code}; the matrix allows it ({row['rule']})")
    else:
        assert str(answer.status_code) == expect, (
            f"{row['route']} answered {role} {answer.status_code}; the matrix says {expect} ({row['rule']})")

"""Response checks for the Portal API contract fuzzing (T-0063, TS-09, UI-05).

Loaded through `SCHEMATHESIS_HOOKS`; every check is named after the requirement it enforces so
a JUnit failure names the requirement and not a generic assertion. Only what the built-in
checks do not already cover lives here: the built-ins validate a response against the published
OpenAPI document, these validate it against the contract in docs/API/01-portal-api.md and the
role a run is made as.
The assertions themselves live in contract.py, which the endpoint leg shares.
"""

from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import schemathesis
import yaml

from contract import leak_violations, problem_json_violations


def _content_type(response: schemathesis.Response) -> str:
    values = response.headers.get("content-type")
    return values[0] if values else ""


@schemathesis.check
def ui05_error_is_problem_json(ctx, response: schemathesis.Response, case) -> None:
    """UI-05 — every 4xx/5xx carries an RFC 7807 body as application/problem+json."""
    label = f"{case.operation.label} -> {response.status_code}"
    violations = problem_json_violations(
        response.status_code,
        _content_type(response),
        response.content,
        label,
    )
    if violations:
        raise AssertionError("\n".join(violations))


@schemathesis.check
def ts09_no_internal_detail_leak(ctx, response: schemathesis.Response, case) -> None:
    """TS-09 — no panic, backtrace, SQL, DSN, server path or token in any response body."""
    label = f"{case.operation.label} -> {response.status_code}"
    violations = leak_violations(response.content, label)
    if violations:
        raise AssertionError("\n".join(violations))


WRITES = {"POST", "PUT", "PATCH", "DELETE"}


@schemathesis.check
def pf50_viewer_never_writes(ctx, response: schemathesis.Response, case) -> None:
    """PF-50 — a run as the viewer (`JC_SCHEMATHESIS_ROLE=viewer`) never gets a 2xx on a write.

    The viewer holds no write verb anywhere, so an accepted write is a missing authorization
    check, a priority-1 finding. Inactive for every other role."""
    if os.environ.get("JC_SCHEMATHESIS_ROLE") != "viewer":
        return
    if case.method.upper() in WRITES and 200 <= response.status_code < 300:
        raise AssertionError(
            f"{case.operation.label} -> {response.status_code}: the viewer wrote (PF-50)"
        )


# The roles a run is checked against the Portal's authorization matrix for (T-5347): a caller
# with no token, and a member of another project. Either one gets a 2xx only where the matrix
# says `allow` for it.
MATRIX_ROLES = ("anonymous", "other-member")
# The Portal's own table; the sandbox keeps the Portal's clone beside this one.
DEFAULT_MATRIX = Path(__file__).resolve().parents[3] / "joinedcontext-portal" / "tests" / "authz_matrix.yaml"


def _route_key(method: str, path: str) -> str:
    """`GET /api/v1/projects/{project}/x` and the matrix's `GET /projects/{name}/x` are one route.

    The matrix names API routes without the `/api/v1` they are served under (the Portal's
    authz_matrix_tests.rs url()); a HEAD is the GET it answers for."""
    method = "GET" if method.upper() == "HEAD" else method.upper()
    path = path.removeprefix("/api/v1") if path.startswith("/api/v1/") else path
    return f"{method} {re.sub(r'{[^}]*}', '{}', path)}"


def _project_of(url: str) -> str | None:
    """The project an API URL names: `/api/v1/projects/<project>/...`."""
    match = re.match(r"/api/v1/projects/([^/]+)", urlsplit(url).path)
    return unquote(match.group(1)) if match else None


@functools.cache
def _allowed(role: str) -> frozenset[str]:
    """The routes the matrix lets `role` pass; a missing table fails the run, never passes it."""
    path = Path(os.environ.get("JC_AUTHZ_MATRIX") or DEFAULT_MATRIX)
    if not path.is_file():
        raise AssertionError(f"the run as {role} needs the Portal's authz_matrix.yaml: set JC_AUTHZ_MATRIX ({path} is missing)")
    table = yaml.safe_load(path.read_text())
    column = table["roles"].index(role)
    return frozenset(
        _route_key(*row["route"].split(" ", 1)) for row in table["routes"] if row["expect"][column] == "allow"
    )


@schemathesis.check
def ac01_no_access_beyond_the_matrix(ctx, response: schemathesis.Response, case) -> None:
    """AC-01, PF-52 — a run as `anonymous` or `other-member` gets a 2xx only where the matrix allows it.

    The other member's own project (`JC_SCHEMATHESIS_OWN_PROJECT`) is theirs to use; a 2xx on any
    other project, or on a route the matrix refuses them, is a missing authorization check.
    Inactive for every other role."""
    role = os.environ.get("JC_SCHEMATHESIS_ROLE")
    if role not in MATRIX_ROLES or not 200 <= response.status_code < 300:
        return
    own = os.environ.get("JC_SCHEMATHESIS_OWN_PROJECT")
    # The URL sent, not the case's parameters: an operation with a literal project in its path
    # has no `project` parameter, and the coverage phase sends other methods to it.
    if own and _project_of(response.request.url) == own:
        return
    # The method sent, not the operation's: the coverage phase also sends unlisted ones (TRACE).
    method = response.request.method or case.method
    if method.upper() == "OPTIONS":
        return  # a CORS preflight: it answers which methods exist, never a resource
    if _route_key(method, case.operation.path) not in _allowed(role):
        raise AssertionError(
            f"{method} {case.operation.path} -> {response.status_code}: the authorization matrix refuses {role} here (AC-01)"
        )

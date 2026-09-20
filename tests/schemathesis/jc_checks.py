"""Response checks for the Portal API contract fuzzing (T-0063, TS-09, UI-05).

Loaded through `SCHEMATHESIS_HOOKS`; every check is named after the requirement it enforces so
a JUnit failure names the requirement and not a generic assertion. Only what the built-in
checks do not already cover lives here: the built-ins validate a response against the published
OpenAPI document, these two validate it against the contract in docs/API/01-portal-api.md.
The assertions themselves live in contract.py, which the endpoint leg shares.
"""

from __future__ import annotations

import schemathesis

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

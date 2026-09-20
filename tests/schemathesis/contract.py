"""The Portal and gateway response contract, independent of any test runner (TS-09, UI-05).

`jc_checks.py` wraps these two functions as schemathesis checks; `test_gateway_endpoints.py`
calls them directly on the endpoint surface, which no OpenAPI document describes. Keeping them
here is what lets the endpoint leg run without the schemathesis package installed.
"""

from __future__ import annotations

import json
import re

# docs/API/01-portal-api.md §2: "All errors return application/problem+json" with the
# type URI under this prefix.
PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_PREFIX = "https://joinedcontext.com/errors/"

# Fingerprints of server internals that must never reach a client, whatever the status code.
LEAKS = [
    ("rust panic", re.compile(r"panicked at|stack backtrace", re.IGNORECASE)),
    ("database dsn", re.compile(r"postgres(?:ql)?://[^\s\"']+")),
    ("sql statement", re.compile(r"\bSELECT\b[\s\S]{0,200}?\bFROM\b", re.IGNORECASE)),
    ("server source path", re.compile(r"(?:/[\w.-]+)+\.rs\b")),
    ("bearer token", re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.")),
]


def problem_json_violations(status_code: int, content_type: str, body: bytes | str, label: str) -> list[str]:
    """Return contract violations if a 4xx/5xx response does not follow RFC 7807 (UI-05)."""
    if status_code < 400:
        return []
    violations: list[str] = []
    if PROBLEM_CONTENT_TYPE not in (content_type or ""):
        violations.append(
            f"UI-05: {label} answered Content-Type {content_type!r}, expected {PROBLEM_CONTENT_TYPE}"
        )
    raw_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    try:
        parsed = json.loads(raw_text)
    except Exception as exc:
        violations.append(f"UI-05: {label} body is not JSON: {exc}")
        return violations
    if not isinstance(parsed, dict):
        violations.append(f"UI-05: {label} body is {type(parsed).__name__}, expected a JSON object")
        return violations
    missing = [field for field in ("type", "title", "status") if field not in parsed]
    if missing:
        violations.append(f"UI-05: {label} problem body misses {', '.join(missing)}: {parsed}")
    if parsed.get("status") != status_code:
        violations.append(f"UI-05: {label} problem body claims status {parsed.get('status')!r}, expected {status_code}")
    type_uri = str(parsed.get("type", ""))
    if not type_uri.startswith(PROBLEM_TYPE_PREFIX):
        violations.append(f"UI-05: {label} problem type {type_uri!r} is outside {PROBLEM_TYPE_PREFIX}")
    return violations


def leak_violations(body: bytes | str, label: str) -> list[str]:
    """Return findings if internal server patterns are disclosed in response text (TS-09)."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    violations: list[str] = []
    for name, pattern in LEAKS:
        match = pattern.search(text)
        if match:
            violations.append(f"TS-09: {label} leaked a {name}: {match.group(0)[:120]!r}")
    return violations

"""Endpoint schema and access surface contract tests (TS-09, EP-46..EP-58, R20)."""

from __future__ import annotations

import os
from typing import Callable

import pytest
import requests

from contract import leak_violations, problem_json_violations

FORBIDDEN_SLUG = "00000000000000000000000000"
UNKNOWN_SLUG = "22222222222222222222222222"


@pytest.fixture(scope="session")
def gateway_url() -> str:
    value = os.environ.get("GATEWAY_URL")
    if not value:
        pytest.skip("GATEWAY_URL is not set — see tests/schemathesis/README.md")
    return value.rstrip("/")


@pytest.fixture(scope="session")
def endpoint_slug() -> str:
    value = os.environ.get("ENDPOINT_SLUG")
    if not value:
        pytest.skip("ENDPOINT_SLUG is not set — see tests/schemathesis/README.md")
    return value.strip()


@pytest.fixture(scope="session")
def schema_major() -> str:
    return os.environ.get("SCHEMA_MAJOR", "v1").strip()


@pytest.fixture(scope="session")
def http_session() -> requests.Session:
    session = requests.Session()
    token = os.environ.get("GATEWAY_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    with session:
        yield session


@pytest.fixture(scope="session")
def request_endpoint(http_session: requests.Session, gateway_url: str) -> Callable[..., requests.Response]:
    def _call(method: str, path: str, **kwargs) -> requests.Response:
        url = f"{gateway_url}/{path.lstrip('/')}"
        res = http_session.request(method, url, timeout=15, **kwargs)
        label = f"{method} {path} -> {res.status_code}"
        leaks = leak_violations(res.content, label)
        assert not leaks, "\n".join(leaks)
        if res.status_code >= 400:
            ctype = res.headers.get("content-type", "")
            errs = problem_json_violations(res.status_code, ctype, res.content, label)
            assert not errs, "\n".join(errs)
        return res

    return _call


@pytest.mark.parametrize(
    "artifact",
    ["model", "schema.json", "context.jsonld", "shapes.ttl", "model.owl", "README.md"],
)
def test_ep46_schema_directory_artifacts(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
    schema_major: str,
    artifact: str,
):
    """EP-46 — schema directory serves documented artifacts with non-empty bodies or 404."""
    path = f"/api/endpoint/{endpoint_slug}/schema/{schema_major}/{artifact}"
    res = request_endpoint("GET", path)
    assert res.status_code in (200, 404), (
        f"EP-46: {path} answered {res.status_code}, expected 200 or 404; "
        f"Content-Type: {res.headers.get('content-type')!r}; body: {res.content[:100]!r}"
    )
    if res.status_code == 200:
        assert len(res.content.strip()) > 0, f"EP-46: {path} returned an empty 200 OK body"


def test_ep49_model_content_negotiation(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
    schema_major: str,
):
    """EP-49 — unversioned model endpoint negotiates YAML, JSON, JSON-LD, Turtle and rejects unknown."""
    formats = {
        "application/yaml": ["yaml", "x-yaml"],
        "application/json": ["json"],
        "application/ld+json": ["ld+json"],
        "text/turtle": ["turtle"],
    }
    bodies: dict[str, bytes] = {}
    path = f"/api/endpoint/{endpoint_slug}/schema/{schema_major}/model"

    for accept_header, expected_tokens in formats.items():
        res = request_endpoint("GET", path, headers={"Accept": accept_header})
        assert res.status_code == 200, (
            f"EP-49: {path} with Accept {accept_header!r} answered {res.status_code}; "
            f"Content-Type: {res.headers.get('content-type')!r}; body: {res.content[:100]!r}"
        )
        ctype = res.headers.get("content-type", "").lower()
        matched = any(tok in ctype for tok in expected_tokens)
        assert matched, (
            f"EP-49: {path} with Accept {accept_header!r} answered Content-Type {ctype!r}, "
            f"expected token in {expected_tokens}"
        )
        bodies[accept_header] = res.content.strip()

    distinct_bodies = {body for body in bodies.values()}
    assert len(distinct_bodies) == len(formats), (
        f"EP-49: {path} returned identical representations across different Accept headers; "
        f"distinct count: {len(distinct_bodies)}/{len(formats)}"
    )

    unsupported = request_endpoint("GET", path, headers={"Accept": "application/x-not-a-format"})
    assert unsupported.status_code == 406, (
        f"EP-49: unservable Accept answered {unsupported.status_code}, expected 406 Not Acceptable; "
        f"Content-Type: {unsupported.headers.get('content-type')!r}; body: {unsupported.content[:100]!r}"
    )


def test_ep48_schema_etag_and_caching(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
    schema_major: str,
):
    """EP-48 — schema artifacts carry strong ETags and immutable Cache-Control; revalidation answers 304."""
    path = f"/api/endpoint/{endpoint_slug}/schema/{schema_major}/model"
    res = request_endpoint("GET", path, headers={"Accept": "application/json"})
    assert res.status_code == 200, f"EP-48: {path} returned {res.status_code}"

    etag = res.headers.get("etag", "").strip()
    assert etag and not etag.startswith("W/"), f"EP-48: expected strong ETag on {path}, got {etag!r}"

    cc = res.headers.get("cache-control", "").lower()
    assert "immutable" in cc, f"EP-48: expected immutable in Cache-Control on {path}, got {cc!r}"

    res_304 = request_endpoint("GET", path, headers={"Accept": "application/json", "If-None-Match": etag})
    assert res_304.status_code == 304, (
        f"EP-48: If-None-Match: {etag} answered {res_304.status_code}, expected 304; "
        f"body: {res_304.content[:100]!r}"
    )
    assert len(res_304.content) == 0, f"EP-48: 304 response had non-empty body ({len(res_304.content)} bytes)"


def test_ep55_ep56_access_surface_default(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
):
    """EP-55, EP-56 — /access default representation returns AuthZEN grant shape as application/json."""
    path = f"/api/endpoint/{endpoint_slug}/access"
    res = request_endpoint("GET", path, headers={"Accept": "application/json"})
    assert res.status_code == 200, (
        f"EP-55: {path} answered {res.status_code}; "
        f"Content-Type: {res.headers.get('content-type')!r}; body: {res.content[:100]!r}"
    )
    ctype = res.headers.get("content-type", "").lower()
    assert "application/json" in ctype, f"EP-56: expected application/json, got {ctype!r}"
    doc = res.json()
    assert isinstance(doc, dict), f"EP-56: expected grant document dict, got {type(doc).__name__}"
    # AuthZEN resource-search shape exposes evaluation grants/evaluations/decision or resources
    has_authzen_keys = any(key in doc for key in ("grants", "evaluations", "decision", "resources", "allowed"))
    assert has_authzen_keys, f"EP-56: {path} does not match AuthZEN grant format: {doc}"


@pytest.mark.parametrize(
    ("accept_header", "expected_token"),
    [
        ("application/odrl+json", "odrl"),
        ("application/vnd.joinedcontext.grant-ast+json", "grant-ast"),
    ],
)
def test_ep57_ep58_access_surface_content_negotiation(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
    accept_header: str,
    expected_token: str,
):
    """EP-57, EP-58 — /access content negotiation supports ODRL and UCAST grant-ast or skips on 406."""
    path = f"/api/endpoint/{endpoint_slug}/access"
    json_res = request_endpoint("GET", path, headers={"Accept": "application/json"})
    assert json_res.status_code == 200, f"EP-55: base json request failed with {json_res.status_code}"

    res = request_endpoint("GET", path, headers={"Accept": accept_header})
    if res.status_code == 406:
        pytest.skip(f"{accept_header} is not supported by deployment (answered 406)")

    assert res.status_code == 200, (
        f"EP-57/EP-58: {path} with Accept {accept_header!r} answered {res.status_code}; "
        f"Content-Type: {res.headers.get('content-type')!r}; body: {res.content[:100]!r}"
    )
    ctype = res.headers.get("content-type", "").lower()
    assert expected_token in ctype, (
        f"EP-57/EP-58: expected {expected_token} in Content-Type, got {ctype!r}"
    )
    assert res.content.strip() != json_res.content.strip(), (
        f"EP-57/EP-58: Accept {accept_header!r} echoed identical JSON body"
    )


@pytest.mark.parametrize(
    "query",
    ["limit=abc", "limit=-1", "limit=99999999999999999999", "limit=" + "9" * 4096, "offset=%00", "offset=-1"],
)
@pytest.mark.parametrize("subpath", ["schema/v1/model", "access"])
def test_ts09_malformed_pagination_parameters(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
    subpath: str,
    query: str,
):
    """TS-09 — a pagination parameter the surface interprets and cannot parse fails closed with 400 or 404."""
    path = f"/api/endpoint/{endpoint_slug}/{subpath}?{query}"
    res = request_endpoint("GET", path)
    assert res.status_code in (400, 404), (
        f"TS-09: malformed {query!r} on {path!r} answered status {res.status_code}, expected 400 or 404; "
        f"Content-Type: {res.headers.get('content-type')!r}; body: {res.content[:100]!r}"
    )


@pytest.mark.parametrize(
    "query",
    [
        "attrs=%ff%fe",
        "format=../../etc/passwd",
        "limit=10&limit=20",
        "q=" + "%22" * 512,
        "%00=%00",
        "type[]=Device&type[]=Sensor",
    ],
)
@pytest.mark.parametrize("subpath", ["schema/v1/model", "access"])
def test_ts09_hostile_query_parameters_never_crash(
    request_endpoint: Callable[..., requests.Response],
    endpoint_slug: str,
    subpath: str,
    query: str,
):
    """TS-09 — a hostile parameter is answered or refused, never with a 5xx and never with a leak.

    A surface may ignore a parameter it does not know, so the assertion is fail-closed rather
    than 400: no 5xx, no leaked internals (`request_endpoint` checks every body), and an error
    is an RFC 7807 problem document, which the same fixture enforces.
    """
    path = f"/api/endpoint/{endpoint_slug}/{subpath}?{query}"
    res = request_endpoint("GET", path)
    assert res.status_code < 500, (
        f"TS-09: hostile query {path!r} crashed the surface with {res.status_code}; body: {res.content[:200]!r}"
    )
    assert b"root:x:" not in res.content and b"/etc/passwd" not in res.content, (
        f"TS-09: {path!r} answered with file content: {res.content[:200]!r}"
    )


@pytest.mark.parametrize(
    "invalid_slug",
    [
        "unknown-slug",
        "../admin",
        "%2e%2e%2fadmin",
        "a" * 257,
        "Slug With Spaces",
        "",
        "slug%00null",
    ],
)
def test_ts09_invalid_slug_permutations(
    request_endpoint: Callable[..., requests.Response],
    invalid_slug: str,
):
    """TS-09 — invalid or nonexistent slug permutations return 400 or 404 problem details, never 5xx."""
    path = f"/api/endpoint/{invalid_slug}/schema/v1/model" if invalid_slug else "/api/endpoint//schema/v1/model"
    res = request_endpoint("GET", path)
    assert res.status_code in (400, 404), (
        f"TS-09: invalid slug path {path!r} answered {res.status_code}, expected 400 or 404; "
        f"Content-Type: {res.headers.get('content-type')!r}; body: {res.content[:100]!r}"
    )


def test_r20_nonexistent_and_forbidden_slug_parity(
    request_endpoint: Callable[..., requests.Response],
):
    """R20 — forbidden and nonexistent endpoint slugs return byte-identical 404 problem details."""
    res_forbidden = request_endpoint("GET", f"/api/endpoint/{FORBIDDEN_SLUG}/access")
    res_unknown = request_endpoint("GET", f"/api/endpoint/{UNKNOWN_SLUG}/access")
    assert res_forbidden.status_code == 404, (
        f"R20: forbidden slug answered {res_forbidden.status_code}, expected 404; body: {res_forbidden.content[:100]!r}"
    )
    assert res_unknown.status_code == 404, (
        f"R20: unknown slug answered {res_unknown.status_code}, expected 404; body: {res_unknown.content[:100]!r}"
    )
    assert res_forbidden.content == res_unknown.content, (
        f"R20: existence disclosure: body for forbidden slug differs from nonexistent slug; "
        f"forbidden: {res_forbidden.content[:100]!r} vs unknown: {res_unknown.content[:100]!r}"
    )

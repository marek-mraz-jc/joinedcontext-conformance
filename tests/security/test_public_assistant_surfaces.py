"""The anonymous surfaces of the public assistant, read-only (T-4147, tasks/GOAL-assistant.md).

Every case only reads, and the rate-limit case spends no model token: an unknown
`conversation` is refused with 400 after the limiter has counted the request and before the
model is called. Each case skips when the variable naming its surface is not set.

| Variable | What |
|---|---|
| `ASSISTANT_URL` | the assistant's public origin, e.g. `https://assistant.{domain}` |
| `ASSISTANT_PUBLIC_ID` | a published anonymous deployment (default `helsinki-public`) |
| `ASSISTANT_ORIGIN` | an origin that deployment allows (default `https://dev.joinedcontext.com`) |
| `ASSISTANT_PER_CLIENT` | its `rateLimit.perClientPerMinute` (default `6`) |
| `MCP_HUB_URL` | the anonymous MCP hub, e.g. `https://portal.{domain}/api/mcp` |
| `CKAN_URL` | the data portal, e.g. `https://data.{domain}` |
"""

import os
import time
import uuid

import pytest
import requests

TIMEOUT = 30
AGENT = {"User-Agent": "jc-conformance/test_public_assistant_surfaces"}


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        pytest.skip(f"environment variable {name} not set")
    return value.rstrip("/")


@pytest.fixture(scope="module")
def assistant() -> str:
    return _env("ASSISTANT_URL")


@pytest.fixture(scope="module")
def public_id() -> str:
    return _env("ASSISTANT_PUBLIC_ID", "helsinki-public")


@pytest.fixture(scope="module")
def allowed_origin() -> str:
    return _env("ASSISTANT_ORIGIN", "https://dev.joinedcontext.com")


def test_internal_paths_are_not_routed_from_outside(assistant: str) -> None:
    """The administration and in-Portal chat paths answer only inside the cluster (AG-113)."""
    for method, path in [
        ("GET", "/internal/v1/projects/helsinki/knowledge/sources"),
        ("GET", "/internal/v1/projects/helsinki/knowledge/deployments/helsinki-public/usage"),
        ("POST", "/internal/v1/projects/helsinki/knowledge/sources/x/recrawl"),
        ("POST", "/internal/v1/projects/helsinki/knowledge/deployments/helsinki-knowledge/chat"),
    ]:
        answer = requests.request(method, assistant + path, headers=AGENT, timeout=TIMEOUT)
        assert answer.status_code in (401, 403, 404), f"{method} {path}: {answer.status_code}"


def test_a_foreign_origin_is_refused_its_preflight(assistant: str, public_id: str) -> None:
    answer = requests.options(
        f"{assistant}/api/v1/d/{public_id}/chat",
        headers={
            **AGENT,
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
        timeout=TIMEOUT,
    )
    assert "access-control-allow-origin" not in {k.lower() for k in answer.headers}, answer.headers
    assert answer.status_code >= 400


def test_the_widget_page_may_be_framed_only_by_its_allowed_origins(
    assistant: str, public_id: str
) -> None:
    answer = requests.get(f"{assistant}/d/{public_id}/widget", headers=AGENT, timeout=TIMEOUT)
    assert answer.status_code == 200
    csp = answer.headers.get("content-security-policy", "")
    assert "frame-ancestors" in csp and "*" not in csp.split("frame-ancestors", 1)[1], csp
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp, csp


def test_a_forged_forwarded_for_buys_no_new_rate_window(
    assistant: str, public_id: str, allowed_origin: str
) -> None:
    """The per-client limit keys on the address the edge saw, not on what the caller wrote."""
    per_client = int(os.getenv("ASSISTANT_PER_CLIENT", "6"))
    time.sleep(61)  # a fresh fixed window for this client
    codes = []
    for i in range(per_client + 2):
        answer = requests.post(
            f"{assistant}/api/v1/d/{public_id}/chat",
            headers={
                **AGENT,
                "Origin": allowed_origin,
                "X-Forwarded-For": f"203.0.113.{i + 1}",
            },
            json={"message": "test", "conversation": str(uuid.uuid4())},
            timeout=TIMEOUT,
        )
        codes.append(answer.status_code)
    assert codes[:per_client] == [400] * per_client, codes
    assert 429 in codes[per_client:], f"a forged X-Forwarded-For bypassed the limit: {codes}"


def test_anonymous_mcp_offers_read_only_tools() -> None:
    """Public MCP is read-only (GOAL-assistant, Security); T-4168 builds it."""
    hub = _env("MCP_HUB_URL")
    answer = requests.post(
        hub,
        headers={**AGENT, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=TIMEOUT,
    )
    assert answer.status_code == 200, answer.text[:300]
    tools = answer.json()["result"]["tools"]
    writes = [t["name"] for t in tools if not t.get("annotations", {}).get("readOnlyHint")]
    assert writes == [], f"an anonymous caller is offered writing tools: {writes}"


def test_ckan_does_not_list_its_accounts_to_anyone() -> None:
    """`user_list` and `user_show` are for a signed-in person; an anonymous visitor learns no
    account name, no last activity and not which account is the sysadmin."""
    ckan = _env("CKAN_URL")
    answer = requests.get(f"{ckan}/api/3/action/user_list", headers=AGENT, timeout=TIMEOUT)
    leaked = answer.status_code == 200 and answer.json().get("result")
    assert not leaked, f"user_list answers anonymously with {len(leaked)} accounts"

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
| `PUBLIC_MCP_URLS` | the public named MCP servers (T-4141), comma-separated |
| `CKAN_URL` | the data portal, e.g. `https://data.{domain}` |

T-4148 runs this file as the gate on the rebuilt assistant (chat page, launcher, widget, public
MCP), beside T-4144's scope and injection gates:

    ASSISTANT_URL=https://assistant.dev.joinedcontext.com ASSISTANT_PUBLIC_ID=… \
      MCP_HUB_URL=… PUBLIC_MCP_URLS=… CKAN_URL=… pytest tests/security/test_public_assistant_surfaces.py
"""

import os
import re
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


# What a token, a key or a cluster address looks like in a shipped file (T-4148).
SECRET_SHAPES = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}\.|sk-[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9._-]{16,}"
    r"|\.svc\.cluster\.local|\b10\.\d+\.\d+\.\d+\b|/internal/v1/"
)


def _framed_only_by_its_origins(answer: requests.Response) -> None:
    assert answer.status_code == 200, answer.status_code
    csp = answer.headers.get("content-security-policy", "")
    assert "frame-ancestors" in csp and "*" not in csp.split("frame-ancestors", 1)[1], csp
    assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp, csp
    assert answer.headers.get("cache-control") == "no-store", answer.headers
    assert "set-cookie" not in {k.lower() for k in answer.headers}, answer.headers


def test_the_full_page_is_held_as_the_panel(assistant: str, public_id: str) -> None:
    """T-4138's full-page chat `/d/{publicId}`: the panel's policy, no cookie, no secret."""
    answer = requests.get(f"{assistant}/d/{public_id}", headers=AGENT, timeout=TIMEOUT)
    _framed_only_by_its_origins(answer)
    assert not SECRET_SHAPES.search(answer.text), SECRET_SHAPES.search(answer.text)


def test_the_launcher_and_scripts_carry_no_secret_and_no_other_host(
    assistant: str, public_id: str
) -> None:
    """The one-line `embed.js` a site includes, and the scripts the page loads, name no token,
    no cluster address and no host but their own."""
    for path in [f"/d/{public_id}/embed.js", "/d/widget.js"]:
        answer = requests.get(assistant + path, headers=AGENT, timeout=TIMEOUT)
        assert answer.status_code == 200, f"{path}: {answer.status_code}"
        assert answer.headers.get("x-content-type-options") == "nosniff", path
        assert "javascript" in answer.headers.get("content-type", ""), path
        found = SECRET_SHAPES.search(answer.text)
        assert not found, f"{path} carries {found.group(0)[:12]}…"
        # A hostname is letters, digits, dots and dashes: `assistant.{domain}` in the usage comment
        # is a placeholder nobody can reach, and stays out; any real host is caught.
        hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)(?![A-Za-z0-9.{-])", answer.text))
        assert hosts <= {"www.w3.org"}, f"{path} names other hosts: {sorted(hosts)}"


def test_an_unpublished_id_has_no_page_no_panel_and_no_launcher(assistant: str) -> None:
    unknown = f"nobody-{uuid.uuid4().hex[:8]}"
    for path in [f"/d/{unknown}", f"/d/{unknown}/widget", f"/d/{unknown}/embed.js"]:
        answer = requests.get(assistant + path, headers=AGENT, timeout=TIMEOUT)
        assert answer.status_code == 404, f"{path}: {answer.status_code}"


def test_an_overlong_question_is_refused_before_anything_is_spent(
    assistant: str, public_id: str, allowed_origin: str
) -> None:
    """Past the service's 4,000 characters (and any deployment's `budget.questionChars`) the
    route refuses with a status, never with an answer stream (AG-98, T-4166)."""
    answer = requests.post(
        f"{assistant}/api/v1/d/{public_id}/chat",
        headers={**AGENT, "Origin": allowed_origin},
        json={"message": "a" * 4_001},
        timeout=TIMEOUT,
    )
    assert answer.status_code in (400, 413, 422), answer.status_code
    assert "text/event-stream" not in answer.headers.get("content-type", ""), answer.headers


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


def _mcp_urls() -> list[str]:
    urls = [os.getenv("MCP_HUB_URL", "")] + os.getenv("PUBLIC_MCP_URLS", "").split(",")
    urls = [u.strip().rstrip("/") for u in urls if u.strip()]
    if not urls:
        pytest.skip("environment variables MCP_HUB_URL and PUBLIC_MCP_URLS not set")
    return urls


def test_anonymous_mcp_offers_read_only_tools() -> None:
    """Public MCP is read-only (GOAL-assistant, Security): the hub (T-4168) and every public
    named server (T-4141)."""
    for url in _mcp_urls():
        answer = requests.post(
            url,
            headers={**AGENT, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=TIMEOUT,
        )
        assert answer.status_code == 200, f"{url}: {answer.text[:300]}"
        tools = answer.json()["result"]["tools"]
        writes = [t["name"] for t in tools if not t.get("annotations", {}).get("readOnlyHint")]
        assert writes == [], f"{url} offers an anonymous caller writing tools: {writes}"


def test_anonymous_mcp_refuses_a_write_it_does_not_offer() -> None:
    """A write named anyway is refused before it reaches the broker (T-4168, T-4141)."""
    for url in _mcp_urls():
        answer = requests.post(
            url,
            headers={**AGENT, "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "delete_subscription", "arguments": {"id": "urn:ngsi-ld:Subscription:none"}},
            },
            timeout=TIMEOUT,
        )
        body = answer.text
        refused = answer.status_code in (400, 401, 403, 404, 405) or '"error"' in body or '"isError":true' in body.replace(" ", "")
        assert refused, f"{url}: {answer.status_code} {body[:300]}"


def test_ckan_does_not_list_its_accounts_to_anyone() -> None:
    """`user_list` and `user_show` are for a signed-in person; an anonymous visitor learns no
    account name, no last activity and not which account is the sysadmin."""
    ckan = _env("CKAN_URL")
    answer = requests.get(f"{ckan}/api/3/action/user_list", headers=AGENT, timeout=TIMEOUT)
    leaked = answer.status_code == 200 and answer.json().get("result")
    assert not leaked, f"user_list answers anonymously with {len(leaked)} accounts"

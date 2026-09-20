"""The credential proxy's refusal matrix, over HTTP (T-0541, AG-34…AG-42, AG-50, AG-52).

Two halves. The structural half runs anywhere and holds the table itself to its shape: every route
the proxy serves has a case, every case names a requirement and says what is wrong with the call.
The live half replays the same table against a running proxy with a real run's ticket, which is
the only way to find out what the proxy does rather than what it is documented to do.

The live half needs `PROXY_URL`, `RUN_ID` and `RUN_TICKET` — a run in `building`, whose profile
declares no write rights and an allow-list that does not include `example.invalid`. Without them
it skips, and says which variable is missing rather than passing quietly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import requests

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from proxy_matrix import (  # noqa: E402
    CASES,
    ROUTES,
    Case,
    is_refused,
    leaks_credentials,
    matrix_violations,
    routes_covered,
)

# ==============================================================================
# The table itself (no deployment)
# ==============================================================================


def test_the_matrix_is_whole():
    """A table with a malformed case would skip a boundary and nobody would see it."""
    problems = matrix_violations()
    assert not problems, "the refusal matrix is not whole:\n" + "\n".join(problems)


def test_every_route_the_proxy_serves_has_a_case():
    """A route with no case is a boundary nothing checks."""
    missing = ROUTES - routes_covered()
    assert not missing, f"no case covers: {sorted(missing)}"


def test_the_two_addresses_that_matter_are_in_the_table():
    """A private range and the metadata service: the two an SSRF reaches for first (AG-50)."""
    queries = " ".join(case.query for case in CASES)
    assert "169.254.169.254" in queries, "nothing asks the proxy for the metadata service"
    assert "10.0.0." in queries, "nothing asks the proxy for an address inside the cluster"


def test_authentication_is_checked_before_anything_else():
    """Every case but the ones about the ticket carries one, or it would refuse for want of a
    ticket and prove nothing about its own rule."""
    for case in CASES:
        if not case.authenticated:
            assert case.requirement == "AG-52", (
                f"{case.id} sends no ticket but is not a case about the ticket"
            )


# ==============================================================================
# The same table, against a running proxy
# ==============================================================================


def _proxy() -> tuple[str, str, str]:
    missing = [name for name in ("PROXY_URL", "RUN_ID", "RUN_TICKET") if not os.getenv(name)]
    if missing:
        pytest.skip(f"the live proxy matrix needs {', '.join(missing)}")
    return (
        os.environ["PROXY_URL"].rstrip("/"),
        os.environ["RUN_ID"],
        os.environ["RUN_TICKET"],
    )


def _send(base: str, case: Case, run: str, ticket: str) -> requests.Response:
    headers = dict(case.headers)
    if case.authenticated:
        headers.setdefault("x-jc-run", run)
        headers.setdefault("x-jc-ticket", ticket)
    elif "x-jc-ticket" in headers:
        headers.setdefault("x-jc-run", run)
    url = f"{base}{case.path}"
    if case.query:
        url = f"{url}?{case.query}"
    kwargs: dict = {"headers": headers, "timeout": 30, "allow_redirects": False}
    if case.json_body is not None:
        kwargs["json"] = case.json_body
    elif case.body is not None:
        headers.setdefault("content-type", "application/json")
        kwargs["data"] = case.body
    return requests.request(case.method, url, **kwargs)


def _frame(response: requests.Response):
    try:
        return response.json()
    except ValueError:
        return None


@pytest.mark.parametrize("case", [c for c in CASES if c.refusal], ids=lambda c: c.id)
def test_the_proxy_refuses_every_call_the_matrix_names(case: Case):
    base, run, ticket = _proxy()
    response = _send(base, case, run, ticket)
    assert response.status_code != 500, (
        f"{case.id} ({case.why}) crashed the proxy: {response.text[:400]}"
    )
    assert is_refused(response.status_code, _frame(response), case.expect), (
        f"{case.id} [{case.requirement}] {case.why}: the proxy answered "
        f"{response.status_code}, expected one of {case.expect}. {response.text[:400]}"
    )


def test_a_control_call_the_proxy_allows_proves_the_refusals_mean_something():
    """A proxy that refused everything would pass every case above without holding one rule."""
    base, run, ticket = _proxy()
    response = requests.get(
        f"{base}/v1/runs/inbox?after=0&wait=0",
        headers={"x-jc-run": run, "x-jc-ticket": ticket},
        timeout=30,
    )
    assert response.status_code == 200, (
        "the run's own inbox was refused, so the refusals above prove nothing: "
        f"{response.status_code} {response.text[:400]}"
    )


def test_the_model_key_is_the_proxys_and_never_the_workspaces():
    """AG-35: a workspace that supplies its own key gets the proxy's behaviour, and the key it
    invented never comes back to it."""
    base, run, ticket = _proxy()
    invented = "sk-ant-the-workspace-invented-this"
    response = requests.post(
        f"{base}/v1/llm/v1/messages",
        headers={
            "x-jc-run": run,
            "x-jc-ticket": ticket,
            "authorization": f"Bearer {invented}",
        },
        json={"model": "claude-sonnet-5", "max_tokens": 1, "messages": []},
        timeout=30,
    )
    assert invented not in response.text, "the proxy echoed the key the workspace supplied"


def test_the_inbox_is_this_runs_and_the_query_cannot_change_that():
    """AG-46: the run comes from the ticket. A workspace asking for another run's inbox is
    answered its own, never the one it named."""
    base, run, ticket = _proxy()
    other = "00000000-0000-0000-0000-000000000000"
    response = requests.get(
        f"{base}/v1/runs/inbox?after=0&wait=0&run={other}",
        headers={"x-jc-run": run, "x-jc-ticket": ticket},
        timeout=30,
    )
    assert response.status_code in (200, 400), response.text[:400]
    assert other not in response.text, "the proxy answered about the run the query named"


def test_no_refusal_carries_a_credential_of_the_deployment():
    """AG-35: a refusal says why, and never with a secret in it. The proxy holds the model key,
    the forge token and its own service-account token; none may reach a workspace."""
    base, run, ticket = _proxy()
    secrets = tuple(
        value
        for value in (
            os.getenv("MODEL_KEY"),
            os.getenv("FORGE_TOKEN"),
            os.getenv("PROXY_TOKEN"),
        )
        if value
    )
    if not secrets:
        pytest.skip("none of MODEL_KEY, FORGE_TOKEN, PROXY_TOKEN is set")
    for case in CASES:
        response = _send(base, case, run, ticket)
        leaked = leaks_credentials(response.text, secrets)
        assert not leaked, f"{case.id} answered with a credential of the deployment in it"

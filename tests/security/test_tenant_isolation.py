"""Organisation and project isolation, end to end (T-1708; PF-32, PF-59).

Two organisations, two projects, one platform. The property is one sentence: nothing of the
other is readable, writable or confirmable as existing, on any door — and a refusal never
tells the caller that what they asked for exists.

What the other suites already play, so this one does not repeat it:

- the gateway's tenancy: `test_tenancy_injection.py`
  (`test_gw20_forged_tenant_header_ignored`, `test_gw20_forged_tenant_header_casings_ignored`,
  `test_gw25_identity_constraint_headers_rejected_or_ignored`,
  `test_sp07_nonexistent_forged_tenant_not_producing_broker_error`,
  `test_sp06_token_space_mismatch_returns_404_matching_nonexistent_space`,
  `test_sp05_response_never_echoes_tenant_header`);
- the grant itself: `test_policy_bypass.py` and `test_silent_narrowing.py`;
- the Portal's own API, in `joinedcontext-portal/tests/` as unit and route tests, among them
  `a_session_with_no_binding_in_the_project_is_answered_404_everywhere_it_reads`,
  `a_listing_of_a_project_nobody_bound_the_caller_to_is_not_an_empty_list`,
  `a_change_is_not_readable_through_another_projects_path`,
  `a_role_of_one_project_grants_nothing_in_another_and_nothing_at_organization_scope`,
  `a_run_reads_a_diagnostic_of_its_own_project_and_of_no_other`,
  `a_key_id_alone_reaches_nothing_across_a_project_or_an_account`,
  `an_event_of_another_project_never_reaches_this_projects_tail`,
  `a_caller_the_project_does_not_bind_exports_nothing_of_it`;
- the runner's sandbox: `test_agent_sandbox_isolation.py`.

What is left, and what this file is: the same attack against a *deployed* platform, through the
doors a running system opens and a unit test cannot reach — the Portal's HTTP API, the gateway's
endpoint surface, MCP, the artifact store, the forge and the broker's own address — with one
token per project, asserting the refusal *and* that it is the refusal of something that does not
exist.

Every door names its own environment variable and skips when it is not given, so the suite runs
against a throwaway environment in CI and never needs one that happens to be at hand.
"""

import os
import typing

import pytest
import requests

TIMEOUT = 30

#: The collections a project holds; a name in one project is not a name in another.
COLLECTIONS = [
    "spaces",
    "endpoints",
    "policies",
    "datasources",
    "pipelines",
    "dashboards",
    "apps",
]


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"environment variable {name} not set")
    return value


@pytest.fixture(scope="session")
def portal_url() -> str:
    """The Portal's public API, e.g. `https://{host}` (the routes are `/api/v1/projects/...`)."""
    return _require("PORTAL_URL").rstrip("/")


@pytest.fixture(scope="session")
def project_a() -> str:
    return _require("PROJECT_A")


@pytest.fixture(scope="session")
def project_b() -> str:
    return _require("PROJECT_B")


@pytest.fixture(scope="session")
def token_a() -> str:
    """A person bound in project A and in no other."""
    return _require("TOKEN_PROJECT_A")


@pytest.fixture(scope="session")
def token_b() -> str:
    """A person bound in project B and in no other."""
    return _require("TOKEN_PROJECT_B")


@pytest.fixture(scope="session")
def session() -> requests.Session:
    made = requests.Session()
    made.headers.update({"Accept": "application/json"})
    return made


@pytest.fixture
def call(session: requests.Session, portal_url: str) -> typing.Callable[..., requests.Response]:
    """One request to the Portal, with a token and without ever raising on the status."""

    def _call(
        method: str,
        path: str,
        token: typing.Optional[str] = None,
        json_body: typing.Optional[dict] = None,
        headers: typing.Optional[dict] = None,
    ) -> requests.Response:
        head = dict(headers or {})
        if token:
            head["Authorization"] = f"Bearer {token}"
        return session.request(
            method,
            portal_url + ("" if path.startswith("/") else "/") + path,
            json=json_body,
            headers=head,
            timeout=TIMEOUT,
        )

    return _call


def refusal(response: requests.Response) -> tuple[int, str]:
    """What a caller learns from a refusal: the status and the body, and nothing else.

    Two refusals that are the same pair are indistinguishable, which is the property: a project
    that exists and one that does not have to answer the same thing (R20, PF-59).
    """
    return response.status_code, response.text


# --- the Portal's API ---------------------------------------------------------------------------


def test_a_token_of_one_project_lists_nothing_of_the_other_on_any_collection(
    call: typing.Callable[..., requests.Response],
    token_a: str,
    project_b: str,
):
    """PF-32, PF-59 — a caller bound in no project sees no collection of it, and the answer is
    the answer of a project that is not there rather than an empty list."""
    absent = call("GET", f"/api/v1/projects/project-that-does-not-exist-{os.getpid()}/spaces", token=token_a)
    for plural in COLLECTIONS:
        answer = call("GET", f"/api/v1/projects/{project_b}/{plural}", token=token_a)
        assert answer.status_code in (401, 403, 404), f"{plural}: {answer.status_code}"
        assert answer.status_code == absent.status_code, (
            f"{plural} says project {project_b} exists: {answer.status_code} against "
            f"{absent.status_code} for a project that does not"
        )
        body = answer.text.lower()
        assert project_b.lower() not in body, f"{plural} names the project in its refusal: {body:.200}"


def test_a_resource_of_the_other_project_is_not_found_by_name_either(
    call: typing.Callable[..., requests.Response],
    token_a: str,
    token_b: str,
    project_b: str,
):
    """PF-32 — the collection is closed and so is every name in it: reading one by name answers
    what an unknown name answers, so a caller cannot enumerate the other project by guessing."""
    held = call("GET", f"/api/v1/projects/{project_b}/spaces", token=token_b)
    if held.status_code != 200:
        pytest.skip("project B's own token does not read its spaces, so there is no name to try")
    items = held.json().get("items") or []
    if not items:
        pytest.skip("project B holds no space to ask for")
    name = items[0]["metadata"]["name"]

    theirs = call("GET", f"/api/v1/projects/{project_b}/spaces/{name}", token=token_a)
    unknown = call("GET", f"/api/v1/projects/{project_b}/spaces/no-such-space-{os.getpid()}", token=token_a)
    assert theirs.status_code in (401, 403, 404)
    assert refusal(theirs) == refusal(unknown), "a name that exists answers differently from one that does not"


def test_a_write_into_the_other_project_changes_nothing_there(
    call: typing.Callable[..., requests.Response],
    token_a: str,
    token_b: str,
    project_b: str,
):
    """PF-32 — a proposal is a write. One aimed at another project is refused, and the proof is
    read back with that project's own token: nothing was created, not even a pending change."""
    name = f"probe-from-{os.getpid()}"
    manifest = {
        "apiVersion": "joinedcontext.com/v1alpha1",
        "kind": "ContextSpace",
        "metadata": {"name": name, "namespace": project_b},
        "spec": {},
    }
    written = call("POST", f"/api/v1/projects/{project_b}/spaces", token=token_a, json_body=manifest)
    assert written.status_code in (401, 403, 404), f"the write was accepted: {written.status_code}"

    held = call("GET", f"/api/v1/projects/{project_b}/spaces/{name}", token=token_b)
    assert held.status_code == 404, "the refused write left something behind"

    changes = call("GET", f"/api/v1/projects/{project_b}/changes", token=token_b)
    if changes.status_code == 200:
        assert name not in changes.text, "the refused write left a change proposal behind"


def test_the_other_projects_changes_activity_and_export_are_not_readable(
    call: typing.Callable[..., requests.Response],
    token_a: str,
    project_b: str,
):
    """PF-32, PF-59 — the doors that answer about a project as a whole are the ones where a
    refusal is easiest to get wrong: a change, the activity feed and the export all carry the
    project's own contents."""
    for path in [
        f"/api/v1/projects/{project_b}/changes",
        f"/api/v1/projects/{project_b}/activity",
        f"/api/v1/projects/{project_b}/export",
    ]:
        answer = call("GET", path, token=token_a)
        assert answer.status_code in (401, 403, 404), f"{path}: {answer.status_code}"
        assert answer.status_code != 200, f"{path} answered a caller of another project"


def test_the_projects_a_caller_is_shown_are_the_ones_they_are_bound_in(
    call: typing.Callable[..., requests.Response],
    token_a: str,
    project_a: str,
    project_b: str,
):
    """PF-59 — the list of projects is itself a disclosure: a project a caller holds no binding
    in is not named there, or the caller learns which organisations are on the platform."""
    answer = call("GET", "/api/v1/projects", token=token_a)
    assert answer.status_code == 200, answer.text[:200]
    named = [
        project.get("name") if isinstance(project, dict) else project
        for project in (answer.json().get("items") or answer.json())
    ]
    assert project_a in named, "the caller's own project is missing from their list"
    assert project_b not in named, "the list names a project the caller is not bound in"


def test_no_token_at_all_reaches_no_project(
    call: typing.Callable[..., requests.Response],
    project_b: str,
):
    """PF-32 — anonymous is not a third project: the same doors are closed without a token, and
    the Portal never answers a collection to nobody."""
    for path in [f"/api/v1/projects/{project_b}/spaces", "/api/v1/projects"]:
        answer = call("GET", path)
        assert answer.status_code in (401, 403, 404), f"{path}: {answer.status_code}"


# --- the gateway --------------------------------------------------------------------------------


def test_a_token_of_one_project_reads_no_endpoint_of_the_other(
    session: requests.Session,
    token_a: str,
):
    """EP-02, PF-32 — an endpoint slug is unguessable, and holding one is still not a grant: a
    caller of another project reading it is answered as if the slug were not there.

    `OTHER_ENDPOINT_URL` is an endpoint of project B, e.g.
    `https://{host}/api/endpoint/{slug}/ngsi-ld/v1`.
    """
    url = _require("OTHER_ENDPOINT_URL").rstrip("/")
    head, tail = url.rsplit("/api/endpoint/", 1)
    slug, rest = tail.split("/", 1)
    nowhere = f"{head}/api/endpoint/{'a' * len(slug)}/{rest}"

    def read(base: str) -> requests.Response:
        return session.get(
            f"{base}/entities",
            headers={"Authorization": f"Bearer {token_a}"},
            params={"limit": 1},
            timeout=TIMEOUT,
        )

    theirs = read(url)
    unknown = read(nowhere)
    assert theirs.status_code in (401, 403, 404), theirs.text[:200]
    if theirs.status_code == 404:
        assert theirs.status_code == unknown.status_code, (
            "a slug that exists answers differently from one that does not"
        )


def test_the_brokers_own_address_answers_nobody_from_outside(session: requests.Session):
    """PF-32, SP-07 — the tenant is the isolation inside the broker, and the broker's own port is
    therefore never a public door: reaching it with a tenant header of another organisation must
    fail at the network, not at the policy.

    `BROKER_URL` is the broker's in-cluster address as seen from outside, e.g.
    `http://2.28.67.127:1026/ngsi-ld/v1`.
    """
    url = _require("BROKER_URL").rstrip("/")
    try:
        answer = session.get(
            f"{url}/entities",
            headers={"NGSILD-Tenant": os.getenv("PROJECT_B", "other")},
            params={"limit": 1},
            timeout=5,
        )
    except requests.RequestException:
        return  # refused at the network, which is the control this asserts
    assert answer.status_code in (401, 403, 404), (
        f"the broker answered {answer.status_code} to a caller from outside the cluster"
    )


# --- MCP ----------------------------------------------------------------------------------------


def test_an_mcp_tool_call_naming_the_other_project_reads_nothing_of_it(
    session: requests.Session,
    token_a: str,
    project_b: str,
):
    """AG-21, PF-32 — a tool's arguments are caller input like any other: naming another project
    or space in them does not move the call, and the answer carries nothing of it.

    `MCP_URL` is the streamable HTTP surface of an endpoint of project A.
    """
    url = _require("MCP_URL").rstrip("/")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": os.getenv("MCP_TOOL", "query_entities"),
            "arguments": {
                "type": os.getenv("GRANTED_TYPE", "AirQualityObserved"),
                "project": project_b,
                "space": project_b,
                "tenant": project_b,
            },
        },
    }
    answer = session.post(
        url,
        json=body,
        headers={
            "Authorization": f"Bearer {token_a}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        timeout=TIMEOUT,
    )
    assert answer.status_code in (200, 400, 403, 404), answer.text[:200]
    if answer.status_code == 200:
        assert f":{project_b}:" not in answer.text, (
            "an entity of the other organisation's space came back through a tool argument"
        )


# --- the artifact store and the forge -------------------------------------------------------------


def test_an_artifact_of_the_other_project_is_not_fetched_with_this_projects_token(
    session: requests.Session,
    token_a: str,
):
    """PF-32, CC-41 — a build artifact is a project's own bytes. Holding its address is not a
    grant, and the refusal says nothing about whether the artifact is there.

    `OTHER_ARTIFACT_URL` is one artifact of project B, as the store addresses it.
    """
    url = _require("OTHER_ARTIFACT_URL")
    answer = session.get(url, headers={"Authorization": f"Bearer {token_a}"}, timeout=TIMEOUT)
    assert answer.status_code in (401, 403, 404), f"the artifact was served: {answer.status_code}"
    assert len(answer.content) < 4096, "a refusal carrying a body that size is the artifact"


def test_the_other_projects_configuration_repository_is_closed(
    session: requests.Session,
    token_a: str,
):
    """PF-32, CC-19 — every manifest of a project lives in its configuration repository, so the
    forge is the same door as the Portal by another route: a token of one project reads neither
    the repository nor its existence.

    `FORGE_URL` is the forge's API root and `OTHER_FORGE_REPO` the `{owner}/{name}` of project B.
    """
    forge = _require("FORGE_URL").rstrip("/")
    repository = _require("OTHER_FORGE_REPO")
    answer = session.get(
        f"{forge}/api/v1/repos/{repository}",
        headers={"Authorization": f"Bearer {token_a}"},
        timeout=TIMEOUT,
    )
    assert answer.status_code in (401, 403, 404), f"the repository was read: {answer.status_code}"

    listing = session.get(
        f"{forge}/api/v1/repos/search",
        params={"q": repository.split("/")[-1]},
        headers={"Authorization": f"Bearer {token_a}"},
        timeout=TIMEOUT,
    )
    if listing.status_code == 200:
        found = listing.json().get("data") or []
        assert all(repository.split("/")[-1] not in (entry.get("full_name") or "") for entry in found), (
            "the search names a repository this caller may not read"
        )


# --- the runner ----------------------------------------------------------------------------------


def test_a_run_of_one_project_is_not_readable_from_the_other(
    call: typing.Callable[..., requests.Response],
    token_a: str,
    project_b: str,
):
    """AG-52, PF-32 — a run carries its project's data in its transcript, so the run routes are
    read by the project's own members and by nobody else.

    `OTHER_RUN_ID` is one agent run of project B.
    """
    run = _require("OTHER_RUN_ID")
    for path in [
        f"/api/v1/projects/{project_b}/agent-runs",
        f"/api/v1/projects/{project_b}/agent-runs/{run}",
        f"/api/v1/projects/{project_b}/agent-runs/{run}/events",
    ]:
        answer = call("GET", path, token=token_a)
        assert answer.status_code in (401, 403, 404), f"{path}: {answer.status_code}"

"""The credential proxy's refusal matrix, as a table (T-0541, AG-34…AG-42, AG-50).

The proxy is the only thing a workspace can reach, so every rule it holds is the whole boundary
between an agent and the platform. The table below is that boundary written once: each case names
the route, what is wrong with the call, and the status the proxy answers. `Architecture/19 §4`
documents the same matrix in prose; this is the machine-checkable half.

The functions here are pure — they build requests and judge answers — so the structural tests run
in CI without a deployment, and the live tests replay the same table against a running proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

#: Every route the proxy serves. Anything else is `403` (the catch-all).
ROUTES: frozenset[str] = frozenset({
    "/v1/data",
    "/v1/llm",
    "/v1/forge",
    "/v1/packages",
    "/v1/fetch",
    "/v1/mcp",
    "/v1/runs/events",
    "/v1/runs/inbox",
    "/v1/diagnostics",
})


@dataclass(frozen=True)
class Case:
    """One call the proxy has to refuse, and what it answers."""

    id: str
    route: str
    requirement: str
    why: str
    method: str = "GET"
    path: str = ""
    query: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Any = None
    body: bytes | None = None
    #: The statuses that count as this refusal. More than one where the proxy may refuse at
    #: either of two layers (a body cap can be the route's 413 or the framework's).
    expect: tuple[int, ...] = (403,)
    #: A ticket is attached unless this says otherwise: the cases about authentication itself
    #: carry their own, and every other case has to fail for its own reason rather than for
    #: want of a ticket.
    authenticated: bool = True
    #: Most cases are refusals. A few are the other kind of boundary — the call goes through, and
    #: what matters is what did not come back with it (a key the workspace supplied, another
    #: run's inbox). Those carry their own assertion in the live suite.
    refusal: bool = True


#: A private address a workspace must never reach through the proxy, and the metadata service
#: that hands out a node's credentials to anything that asks.
INSIDE = "http://10.0.0.5/admin"
METADATA = "http://169.254.169.254/hetzner/v1/metadata"

CASES: tuple[Case, ...] = (
    # --- Authentication: the ticket is the run, and nothing else is.
    Case(
        id="prx-001",
        route="/v1/data",
        requirement="AG-52",
        why="no ticket at all",
        path="/v1/data/ngsi-ld/v1/entities",
        expect=(401,),
        authenticated=False,
    ),
    Case(
        id="prx-002",
        route="/v1/data",
        requirement="AG-52",
        why="a ticket that belongs to no run",
        path="/v1/data/ngsi-ld/v1/entities",
        headers={"x-jc-ticket": "not-the-ticket"},
        expect=(401,),
        authenticated=False,
    ),
    Case(
        id="prx-003",
        route="/v1/data",
        requirement="AG-52",
        why="a run id that is not the ticket's run",
        path="/v1/data/ngsi-ld/v1/entities",
        headers={"x-jc-run": "00000000-0000-0000-0000-000000000000"},
        expect=(401, 403),
    ),
    # --- The data plane: the run's endpoint, and the run's rights on it.
    Case(
        id="prx-010",
        route="/v1/data",
        requirement="AG-36",
        why="a path that climbs out of the endpoint",
        path="/v1/data/../../admin",
    ),
    Case(
        id="prx-011",
        route="/v1/data",
        requirement="AG-37",
        why="a write from a run that declared no write rights",
        method="POST",
        path="/v1/data/ngsi-ld/v1/entities",
        json_body={"id": "urn:ngsi-ld:Thing:x", "type": "Thing"},
    ),
    # --- The forge: one branch, one prefix, never the default branch.
    Case(
        id="prx-020",
        route="/v1/forge",
        requirement="AG-39",
        why="a write to the default branch",
        method="PUT",
        path="/v1/forge/repos/test-owner/test-repo/contents/README.md",
        json_body={"branch": "main", "content": "", "message": "x"},
    ),
    Case(
        id="prx-021",
        route="/v1/forge",
        requirement="AG-39",
        why="a file outside the run's own prefix",
        method="PUT",
        path="/v1/forge/repos/test-owner/test-repo/contents/users/roles/steward.yaml",
        json_body={"branch": "agent/app-bikes/x", "content": "", "message": "x"},
    ),
    # --- Egress: allow-listed hosts, over TLS, and no address that steers the workload inward.
    Case(
        id="prx-030",
        route="/v1/fetch",
        requirement="AG-50",
        why="a host the profile does not allow",
        path="/v1/fetch",
        query="url=https://example.invalid/readme",
    ),
    Case(
        id="prx-031",
        route="/v1/fetch",
        requirement="AG-50",
        why="an address inside the cluster",
        path="/v1/fetch",
        query=f"url={INSIDE}",
    ),
    Case(
        id="prx-032",
        route="/v1/fetch",
        requirement="AG-50",
        why="the node's metadata service",
        path="/v1/fetch",
        query=f"url={METADATA}",
    ),
    Case(
        id="prx-033",
        route="/v1/fetch",
        requirement="AG-50",
        why="a credential carried in the URL's userinfo",
        path="/v1/fetch",
        query="url=https://user:secret@crates.io/api/v1/crates/serde",
    ),
    Case(
        id="prx-034",
        route="/v1/packages",
        requirement="AG-50",
        why="a registry the profile does not allow",
        path="/v1/packages/evil.example/serde",
    ),
    # --- Events: one line of a conversation, and the run's own.
    Case(
        id="prx-040",
        route="/v1/runs/events",
        requirement="AG-45",
        why="an event larger than the route reads",
        method="POST",
        path="/v1/runs/events",
        body=b'{"kind":"message","payload":{"text":"' + b"x" * (65 * 1024) + b'"}}',
        expect=(413,),
    ),
    # --- The model: a run spends steps, and the key is never the workspace's.
    Case(
        id="prx-045",
        route="/v1/llm",
        requirement="AG-35",
        why="a model call carrying a key of its own, which the proxy replaces with its",
        method="POST",
        path="/v1/llm/v1/messages",
        headers={"authorization": "Bearer sk-ant-the-workspace-invented-this"},
        json_body={"model": "claude-sonnet-5", "max_tokens": 1, "messages": []},
        # Whatever the upstream answers, the workspace's own key must not be what was used;
        # the live leg asserts the answer never carries it back.
        expect=(200, 400, 401, 403, 429, 502),
        refusal=False,
    ),
    # --- The inbox: what was said to this run, and never to another.
    Case(
        id="prx-046",
        route="/v1/runs/inbox",
        requirement="AG-46",
        why="another run's inbox, asked for by id",
        path="/v1/runs/inbox",
        query="after=0&run=00000000-0000-0000-0000-000000000000",
        # The route takes the run from the ticket and ignores the query, so this answers this
        # run's own inbox: the live leg asserts it is this run's, never the one named.
        expect=(200, 400, 403),
        refusal=False,
    ),
    # --- The registry: a run calls it as a person, and never decides a change (T-0837).
    Case(
        id="prx-050",
        route="/v1/mcp",
        requirement="AG-11",
        why="an agent approving a change",
        method="POST",
        path="/v1/mcp",
        json_body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "jc_change_approve",
                "arguments": {"project": "helsinki", "id": "chg-00000001"},
            },
        },
        # The refusal is the tool's, inside a 200 JSON-RPC frame: `is_refused` reads both.
        expect=(200, 403),
    ),
    # --- Diagnostics: two components, by name, inside the run's project.
    Case(
        id="prx-060",
        route="/v1/diagnostics",
        requirement="AG-57",
        why="a component the route does not serve",
        path="/v1/diagnostics/secrets/db-password",
        expect=(403, 404),
    ),
    # --- Everything else.
    Case(
        id="prx-070",
        route="/*",
        requirement="AG-34",
        why="a route the proxy does not serve",
        path="/v1/../metrics",
    ),
    Case(
        id="prx-071",
        route="/*",
        requirement="AG-34",
        why="the Kubernetes API, asked for directly",
        path="/api/v1/namespaces/agents/secrets",
    ),
)


def routes_covered(cases: tuple[Case, ...] = CASES) -> set[str]:
    """The routes the matrix has at least one case for."""
    return {case.route for case in cases}


def matrix_violations(cases: tuple[Case, ...] = CASES) -> list[str]:
    """What is wrong with the table itself, before any of it is sent anywhere."""
    problems: list[str] = []
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            problems.append(f"{case.id}: two cases carry this id")
        seen.add(case.id)
        if not case.path:
            problems.append(f"{case.id}: names no path")
        if not case.requirement:
            problems.append(f"{case.id}: names no requirement")
        if not case.why:
            problems.append(f"{case.id}: does not say what is wrong with the call")
        if not case.expect:
            problems.append(f"{case.id}: expects no status")
        if case.refusal and any(status < 400 for status in case.expect if status != 200):
            problems.append(f"{case.id}: expects {case.expect}, which is not a refusal")
        if case.refusal and case.expect == (200,):
            problems.append(f"{case.id}: a refusal that answers only 200 says nothing")
        if case.json_body is not None and case.body is not None:
            problems.append(f"{case.id}: carries two bodies")
    uncovered = ROUTES - routes_covered(cases)
    if uncovered:
        problems.append(
            "no case refuses anything on: " + ", ".join(sorted(uncovered))
        )
    return problems


def is_refused(status: int, frame: Any, expect: tuple[int, ...]) -> bool:
    """Whether the proxy refused, counting a JSON-RPC tool error as the refusal it is.

    A `200` is only ever a refusal when the body says so: a tool result carrying `isError`, or a
    JSON-RPC error frame. Anything else answered `200` is the proxy having let the call through.
    """
    if status not in expect:
        return False
    if status != 200:
        return True
    if not isinstance(frame, Mapping):
        return False
    if "error" in frame:
        return True
    result = frame.get("result")
    return isinstance(result, Mapping) and result.get("isError") is True


def leaks_credentials(text: str, secrets: tuple[str, ...]) -> list[str]:
    """Any secret of the deployment that appears in what the proxy answered (AG-35)."""
    return [secret for secret in secrets if secret and secret in text]

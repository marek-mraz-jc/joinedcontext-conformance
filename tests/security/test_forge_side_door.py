"""The forge as a side door to the configuration (T-1703, CC-41, PF-51).

Every change to the platform's configuration is a Change with a Verdict, and the forge is where
that Change ends up. If the forge itself can be driven directly — forked, pushed to, opened a
pull request on, or read across organizations — then the Verdict is decoration and the review
trail is a side door with a signpost.

The attack is played as the least privileged identity the platform has: a person who is signed
in through Keycloak, landed in the read-only forge team, and holds a token of their own. Each
case sends one request and asserts the refusal, its status code, and that nothing was created.

This suite writes. It runs against a throwaway forge in CI and never against dev; without
`FORGE_URL` and `FORGE_READER_TOKEN` every case skips itself rather than aiming somewhere else.
`selftest_forge.py` is what proves these cases can go red: it runs them against a stub forge
with one door open at a time.
"""

from __future__ import annotations

import typing

import pytest
import requests

Call = typing.Callable[..., requests.Response]

# A refusal is 401, 403 or 404. 404 is honest for a private thing the caller is not in: a 403
# confirms it exists. Anything in the 2xx range is the door standing open.
REFUSED = (401, 403, 404, 405, 422)


def _refusal(res: requests.Response, what: str) -> None:
    assert res.status_code in REFUSED, (
        f"{what}: the forge answered {res.status_code}, so the door is open. "
        f"Body: {res.text[:300]}"
    )


def test_cc41_a_reader_cannot_fork_the_configuration_repository(
    forge: Call, forge_org: str, forge_repo: str
):
    """CC-41 — a fork is a personal copy of the private configuration that leaving the Keycloak
    group does not take away, and the forge offers every reader "fork to propose changes"
    (T-1461). The defence is MAX_CREATION_LIMIT=0 with ALLOW_FORK_WITHOUT_MAXIMUM_LIMIT=false."""
    res = forge("POST", f"/api/v1/repos/{forge_org}/{forge_repo}/forks", json_body={})
    _refusal(res, "a reader forked the configuration repository")


def test_cc41_a_reader_cannot_create_a_repository_of_their_own(
    forge: Call, forge_org: str
):
    """CC-41 — the fork limit is a repository-creation limit, so a reader who can create a
    repository can also copy one into it. Both doors, the personal one and the organization's."""
    for path in ("/api/v1/user/repos", f"/api/v1/orgs/{forge_org}/repos"):
        res = forge("POST", path, json_body={"name": "t-1703-side-door", "private": True})
        _refusal(res, f"a reader created a repository through {path}")


def test_cc41_pf51_the_default_branch_is_protected_and_a_reader_may_neither_push_nor_merge(
    forge: Call, forge_org: str, forge_repo: str, forge_branch: str
):
    """PF-51 — the branch the platform reads its configuration from is protected, and the forge
    reports for the calling identity whether it may push or merge. A reader may do neither: the
    Portal's Verdict is the only thing that moves the default branch (CC-41)."""
    res = forge("GET", f"/api/v1/repos/{forge_org}/{forge_repo}/branches/{forge_branch}")
    assert res.status_code == 200, f"the reader cannot see the branch at all: {res.status_code}"
    branch = res.json()
    assert branch.get("protected") is True, (
        f"{forge_branch} carries no branch protection, so every identity with write access "
        "pushes straight past the Change and its Verdict"
    )
    assert branch.get("user_can_push") is False, "a reader may push to the default branch"
    assert branch.get("user_can_merge") is False, "a reader may merge into the default branch"


def test_cc41_a_reader_cannot_write_a_file_on_the_default_branch(
    forge: Call, forge_org: str, forge_repo: str, forge_branch: str
):
    """CC-41 — the contents API is a push without a clone: one PUT commits on the named branch.
    A reader's commit on the default branch would be a configuration change with no Change."""
    res = forge(
        "PUT",
        f"/api/v1/repos/{forge_org}/{forge_repo}/contents/.t-1703-probe.txt",
        json_body={
            "branch": forge_branch,
            "content": "dDE3MDM=",
            "message": "T-1703 probe, expected to be refused",
        },
    )
    _refusal(res, "a reader committed on the default branch")


def test_cc41_a_reader_cannot_open_a_pull_request_outside_the_portal(
    forge: Call, forge_org: str, forge_repo: str, forge_branch: str
):
    """CC-41 — a pull request opened in the forge carries no Change, so nothing records who
    asked for the configuration to change or what the Verdict was. The Portal opens them."""
    res = forge(
        "POST",
        f"/api/v1/repos/{forge_org}/{forge_repo}/pulls",
        json_body={
            "base": forge_branch,
            "head": "t-1703-side-door",
            "title": "T-1703 probe, expected to be refused",
        },
    )
    _refusal(res, "a reader opened a pull request in the forge")


def test_cc41_a_reader_cannot_push_to_the_branch_a_pull_request_is_reviewed_from(
    forge: Call, forge_org: str, forge_repo: str
):
    """CC-41 — editing a pull request after its Verdict means pushing a commit onto the branch
    the approved diff was read from. A reader has no push access to any branch of the
    repository, so the head of a Change under review is out of reach as well as `main`."""
    res = forge(
        "PUT",
        f"/api/v1/repos/{forge_org}/{forge_repo}/contents/.t-1703-probe.txt",
        json_body={
            "branch": "jc/change-under-review",
            "new_branch": "jc/change-under-review",
            "content": "dDE3MDM=",
            "message": "T-1703 probe, expected to be refused",
        },
    )
    _refusal(res, "a reader committed on a branch a Change is reviewed from")


def test_pf51_the_protection_rule_pins_the_pusher_and_drops_an_approval_a_new_commit_invalidates(
    forge: Call,
    forge_org: str,
    forge_repo: str,
    forge_branch: str,
    forge_platform_token: typing.Optional[str],
):
    """PF-51, CC-41 — the rule itself, read with the credential that administers the repository.
    Three properties make the Verdict mean something: the push whitelist, so only the Portal's
    identity moves the branch; `dismiss_stale_approvals`, so a commit pushed after the Verdict
    does not inherit it; and `block_on_outdated_branch`, so an approved diff is not merged over
    a base it was never reviewed against."""
    if not forge_platform_token:
        pytest.skip("FORGE_PLATFORM_TOKEN is not set: the protection rule is not readable")

    res = forge(
        "GET",
        f"/api/v1/repos/{forge_org}/{forge_repo}/branch_protections",
        token=forge_platform_token,
    )
    assert res.status_code == 200, f"the protection rules are not readable: {res.status_code}"
    rules = res.json()
    assert isinstance(rules, list), f"expected a list of rules, got {rules!r:.200}"

    rule = next(
        (r for r in rules if r.get("rule_name") in (forge_branch, "*")),
        None,
    )
    assert rule is not None, (
        f"no branch protection rule covers {forge_branch}: every identity with write access "
        "pushes past the Change and its Verdict"
    )
    assert rule.get("enable_push_whitelist") is True and rule.get("push_whitelist_usernames"), (
        "the rule lets anybody with write access push; only the Portal's identity may"
    )
    assert rule.get("required_approvals", 0) >= 1, "the branch merges without a Verdict"
    assert rule.get("dismiss_stale_approvals") is True, (
        "a commit pushed after the Verdict keeps the approval it was never given"
    )
    assert rule.get("block_on_outdated_branch") is True, (
        "an approved diff merges over a base it was not reviewed against"
    )


def test_cc41_a_reader_cannot_read_another_organisations_repository(
    forge: Call, forge_other_org: str, forge_other_repo: str
):
    """CC-41 — one forge holds every organization's configuration, and the team map puts a
    person in the teams of their own. Another organization's repository answers 404 and not
    403: a 403 confirms that it is there, which is the first half of reading it."""
    res = forge("GET", f"/api/v1/repos/{forge_other_org}/{forge_other_repo}")
    assert res.status_code == 404, (
        f"another organization's repository answered {res.status_code}; 404 is the only answer "
        "that neither serves it nor confirms it exists"
    )

    contents = forge(
        "GET", f"/api/v1/repos/{forge_other_org}/{forge_other_repo}/contents/README.md"
    )
    assert contents.status_code == 404, (
        f"a file of another organization answered {contents.status_code}"
    )


def test_cc41_a_reader_cannot_read_or_add_a_webhook_on_the_configuration_repository(
    forge: Call, forge_org: str, forge_repo: str
):
    """CC-41 — a repository webhook is a copy of every push sent to an address of the caller's
    choosing, and its configuration carries the shared secret the Portal verifies. Reading the
    hooks leaks that secret; adding one exfiltrates the configuration on every merge."""
    listed = forge("GET", f"/api/v1/repos/{forge_org}/{forge_repo}/hooks")
    _refusal(listed, "a reader listed the repository's webhooks")

    added = forge(
        "POST",
        f"/api/v1/repos/{forge_org}/{forge_repo}/hooks",
        json_body={
            "type": "gitea",
            "active": True,
            "events": ["push"],
            "config": {"url": "http://127.0.0.1:9/t-1703", "content_type": "json"},
        },
    )
    _refusal(added, "a reader added a webhook to the configuration repository")


def test_pf51_a_leaked_platform_token_cannot_administer_the_forge(
    forge: Call, forge_org: str, forge_platform_token: typing.Optional[str]
):
    """PF-51 — the Portal's forge credential is a Secret in the cluster, and a Secret that is
    read is a Secret that has leaked. What it is worth is the question: a token on a dedicated
    machine user reaches one repository with one verb, a token minted on the site administrator
    reaches every repository of every organization and the administration API besides."""
    if not forge_platform_token:
        pytest.skip("FORGE_PLATFORM_TOKEN is not set: the leaked credential cannot be played")

    for method, path, body in (
        ("GET", "/api/v1/admin/users", None),
        ("POST", "/api/v1/orgs", {"username": "t-1703-side-door"}),
        ("POST", "/api/v1/user/repos", {"name": "t-1703-side-door", "private": True}),
        ("POST", f"/api/v1/orgs/{forge_org}/repos", {"name": "t-1703-side-door", "private": True}),
    ):
        res = forge(method, path, token=forge_platform_token, json_body=body)
        _refusal(res, f"the leaked platform token reached {method} {path}")

"""A stub of the forge's API, hardened or with one planted side door (T-1703, CC-41, PF-51).

`test_forge_side_door.py` plays the attack against a real forge. This stub is what proves the
suite can go red: `hardened` answers the way the configuration repository's forge must, and each
other mode opens exactly one of the doors the attack knocks on, so `selftest_forge.py` can show
which case notices it. Only the endpoints the suite calls are implemented; anything else is 404,
which is also what a forge answers for a path that is not its API.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

ORG = "joinedcontext"
REPO = "configuration"
BRANCH = "main"
OTHER_ORG = "mesto-kosice"
OTHER_REPO = "configuration"

READER_TOKEN = "reader-token"
PLATFORM_TOKEN = "platform-token"

# Every door the attack tries, and the mode that opens it. `hardened` opens none.
MODES = (
    "hardened",
    "fork_allowed",
    "repo_creation_allowed",
    "branch_unprotected",
    "pull_request_outside_the_portal",
    "reader_may_push_a_branch",
    "cross_organisation_readable",
    "reader_may_add_a_webhook",
    "platform_token_is_an_administrator",
    "protection_without_review_rules",
)

HARDENED_PROTECTION: dict[str, Any] = {
    "rule_name": BRANCH,
    "enable_push": True,
    "enable_push_whitelist": True,
    "push_whitelist_usernames": ["jc-portal"],
    "push_whitelist_deploy_keys": False,
    "enable_merge_whitelist": True,
    "merge_whitelist_usernames": ["jc-portal"],
    "required_approvals": 1,
    "enable_approvals_whitelist": False,
    "dismiss_stale_approvals": True,
    "block_on_outdated_branch": True,
    "block_on_rejected_reviews": True,
    "block_on_official_review_requests": True,
}


class StubForgeHandler(BaseHTTPRequestHandler):
    mode = "hardened"
    body: dict[str, Any] = {}

    def log_message(self, *_args: Any) -> None:  # keep the selftest output readable
        return

    # -- helpers ---------------------------------------------------------------

    def _token(self) -> str:
        value = self.headers.get("Authorization", "")
        for prefix in ("token ", "Bearer "):
            if value.startswith(prefix):
                return value[len(prefix):].strip()
        return ""

    def _send(self, status: int, body: Any = None) -> None:
        payload = json.dumps(body if body is not None else {}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _refuse(self, status: int = 403, message: str = "the forge refuses this") -> None:
        # Gitea answers a refusal with a message; the suite reads the code, not the prose.
        self._send(status, {"message": message, "url": "https://docs.gitea.com/api"})

    def _is_reader(self) -> bool:
        return self._token() == READER_TOKEN

    def _is_platform(self) -> bool:
        return self._token() == PLATFORM_TOKEN

    def _known_token(self) -> bool:
        return self._is_reader() or self._is_platform()

    # -- routing ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        self._route("GET", urlparse(self.path).path)

    def do_POST(self) -> None:  # noqa: N802
        self._read_body()
        self._route("POST", urlparse(self.path).path)

    def do_PUT(self) -> None:  # noqa: N802
        self._read_body()
        self._route("PUT", urlparse(self.path).path)

    def _read_body(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            parsed = {}
        self.body = parsed if isinstance(parsed, dict) else {}

    def _route(self, method: str, path: str) -> None:
        if not self._known_token():
            self._refuse(401, "token does not exist")
            return

        repo = f"/api/v1/repos/{ORG}/{REPO}"
        other = f"/api/v1/repos/{OTHER_ORG}/{OTHER_REPO}"

        if method == "GET" and path == f"{repo}/branches/{BRANCH}":
            self._branch()
        elif method == "GET" and path == f"{repo}/branch_protections":
            self._branch_protections()
        elif method == "POST" and path == f"{repo}/forks":
            self._fork()
        elif method == "POST" and path in ("/api/v1/user/repos", f"/api/v1/orgs/{ORG}/repos"):
            self._create_repository()
        elif method == "POST" and path == "/api/v1/orgs":
            self._create_organisation()
        elif method == "PUT" and path.startswith(f"{repo}/contents/"):
            self._write_contents()
        elif method == "POST" and path == f"{repo}/pulls":
            self._open_pull_request()
        elif method in ("GET", "POST") and path == f"{repo}/hooks":
            self._hooks(method)
        elif method == "GET" and path == "/api/v1/admin/users":
            self._admin_users()
        elif method == "GET" and path in (other, f"{other}/contents/README.md"):
            self._other_organisation()
        elif method == "GET" and path == repo:
            self._send(200, {"full_name": f"{ORG}/{REPO}", "private": True, "fork": False})
        else:
            self._refuse(404, "Not Found")

    # -- the doors -------------------------------------------------------------

    def _branch(self) -> None:
        protected = self.mode != "branch_unprotected"
        self._send(200, {
            "name": BRANCH,
            "protected": protected,
            # Gitea reports these for the authenticated caller, which is what makes the
            # reader's own probe enough: no administrator token is needed to see the refusal.
            "user_can_push": not protected or self._is_platform(),
            "user_can_merge": not protected or self._is_platform(),
        })

    def _branch_protections(self) -> None:
        if not self._is_platform():
            self._refuse(403, "the caller does not administer this repository")
            return
        if self.mode == "branch_unprotected":
            self._send(200, [])
            return
        rule = dict(HARDENED_PROTECTION)
        if self.mode == "protection_without_review_rules":
            # The rule exists and pins the pusher, but a commit pushed after the Verdict keeps
            # the approval it was not given: this is "edit a pull request after its Verdict".
            rule["required_approvals"] = 0
            rule["dismiss_stale_approvals"] = False
            rule["block_on_outdated_branch"] = False
        self._send(200, [rule])

    def _fork(self) -> None:
        if self.mode == "fork_allowed":
            self._send(202, {"full_name": f"reader/{REPO}", "fork": True})
        else:
            self._refuse(403, "the maximum number of repositories for this user is reached")

    def _create_repository(self) -> None:
        if self.mode == "repo_creation_allowed" or (
            self.mode == "platform_token_is_an_administrator" and self._is_platform()
        ):
            self._send(201, {"full_name": "reader/side-door", "private": False})
        else:
            self._refuse(403, "the maximum number of repositories for this user is reached")

    def _create_organisation(self) -> None:
        if self.mode == "platform_token_is_an_administrator" and self._is_platform():
            self._send(201, {"username": "side-door"})
        else:
            self._refuse(403, "the token does not carry the scope this needs")

    def _admin_users(self) -> None:
        if self.mode == "platform_token_is_an_administrator" and self._is_platform():
            self._send(200, [{"login": "gitea_admin", "is_admin": True}])
        else:
            self._refuse(403, "the token does not carry the scope this needs")

    def _write_contents(self) -> None:
        if self._is_platform():
            self._send(200, {"content": {"path": "written-by-the-portal"}})
            return
        on_default = self.body.get("branch", BRANCH) == BRANCH
        opened = (self.mode == "branch_unprotected" and on_default) or (
            self.mode == "reader_may_push_a_branch" and not on_default
        )
        if opened:
            self._send(201, {"content": {"path": "written-by-a-reader"}})
        else:
            self._refuse(403, "user does not have push access to the repository")

    def _open_pull_request(self) -> None:
        if self.mode == "pull_request_outside_the_portal":
            self._send(201, {"number": 41, "state": "open"})
        else:
            self._refuse(403, "user does not have permission to open a pull request here")

    def _hooks(self, method: str) -> None:
        if self.mode == "reader_may_add_a_webhook":
            self._send(200 if method == "GET" else 201, [] if method == "GET" else {"id": 7})
        else:
            self._refuse(403, "user does not administer this repository")

    def _other_organisation(self) -> None:
        if self.mode == "cross_organisation_readable":
            self._send(200, {"full_name": f"{OTHER_ORG}/{OTHER_REPO}", "private": True})
        else:
            # 404, never 403: a private repository of another organization does not exist
            # for a caller who is not in it, and 403 would confirm that it does.
            self._refuse(404, "Not Found")


class StubForgeServer:
    def __init__(self, httpd: HTTPServer, thread: threading.Thread) -> None:
        self.httpd = httpd
        self.thread = thread
        self.port: int = httpd.server_address[1]
        self.url: str = f"http://127.0.0.1:{self.port}"

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def serve(port: int = 0, mode: str = "hardened") -> StubForgeServer:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; one of {MODES}")
    handler_cls = type(f"StubForgeHandler_{mode}", (StubForgeHandler,), {"mode": mode})
    httpd = HTTPServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return StubForgeServer(httpd, thread)

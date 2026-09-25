"""T-2797: which cells of the Portal's authorization matrix the live suite sends to dev.

A read goes for every role; a write only where its row says `live: true`, so a check that came
loose on dev writes nothing there; the internal listener and /metrics are never sent.
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "authz"
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("authz_live", HERE / "test_authz_live.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
sys.path.remove(str(HERE))

ROLES = ["anonymous", "viewer", "editor", "steward", "approver", "org-admin", "other-member", "service-account"]
TABLE = {"roles": ROLES, "routes": [
    {"route": "GET /projects/{project}", "rule": "project-read", "expect": ["401"] + ["allow"] * 5 + ["404", "allow"]},
    {"route": "DELETE /projects/{project}", "rule": "project-verb:delete:Project", "expect": ["403"] * 8},
    {"route": "POST /projects/{project}/{plural}", "rule": "p", "expect": ["403"] * 8, "live": True},
    {"route": "GET /internal/previews", "rule": "workload:gateway", "expect": ["401"] * 8},
    {"route": "GET /metrics", "rule": "public", "expect": ["allow"] * 8},
]}


def test_reads_and_live_writes_only():
    live.TABLE = TABLE
    sent = {(param.values[1], param.values[2]) for param in live.cells()}
    assert sent == {("GET", "/projects/{project}"), ("POST", "/projects/{project}/{plural}")}
    assert sum(1 for _ in live.cells()) == 16


def test_placeholders_are_objects_that_do_not_exist():
    url = live.url("https://portal", "/projects/{project}/{plural}/{name}")
    assert url == "https://portal/api/v1/projects/helsinki/endpoints/authz-probe-missing"
    assert live.url("https://portal", "/apps/{name}/") == "https://portal/apps/authz-probe-missing/"

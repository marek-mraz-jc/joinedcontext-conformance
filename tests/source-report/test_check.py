"""tests/source-report/check.py (T-4892): the nightly source-vs-space check of every snapshot pipeline.

A Portal stand-in on a local port answers the pipelines and their reports; each case is judged as
the check would on dev.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "source_report", Path(__file__).parent / "check.py"
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
FRESH = {"run": "r9", "at": "2026-10-21T03:04:00Z", "read": 48}


def report(**over):
    return {
        "ledger": 47,
        "orphans": 0,
        "missing": 0,
        "hold": None,
        "lastComplete": FRESH,
        **over,
    }


def test_a_sound_report_passes_and_each_defect_is_named():
    age, cap = timedelta(hours=48), 10
    assert check.judge(report(), NOW, age, cap) == []
    held = check.judge(
        report(
            hold={"run": "r9", "reason": "share-exceeded", "would": 6, "ledger": 50}
        ),
        NOW,
        age,
        cap,
    )
    assert held == [
        "run r9 is held (share-exceeded): it would delete 6 of 50 entities and deleted nothing"
    ]
    assert check.judge(report(lastComplete=None), NOW, age, cap) == [
        "no run has read the source completely"
    ]
    stale = check.judge(
        report(lastComplete={**FRESH, "at": "2026-10-18T03:00:00Z"}), NOW, age, cap
    )
    assert stale and "older than" in stale[0]
    assert check.judge(report(missing=11), NOW, age, cap) == [
        "11 records of the last complete run are missing from the space (refused)"
    ]
    # Orphans are the rule at work between two runs, never a red case on their own.
    assert check.judge(report(orphans=3, orphanIds=["urn:x:1"]), NOW, age, cap) == []


@pytest.fixture
def portal():
    answers: dict[str, tuple[int, object]] = {}
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization", ""))
            status, body = answers.get(self.path, (404, {"detail": "not found"}))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", answers, seen
    server.shutdown()


def pipeline(name, mode):
    return {"metadata": {"name": name}, "spec": {"sourceDeletes": {"mode": mode}}}


def test_every_snapshot_pipeline_is_one_case_and_others_are_skipped(portal):
    base, answers, seen = portal
    answers["/api/v1/projects/helsinki/pipelines"] = (
        200,
        {
            "items": [
                pipeline("registry", "snapshot"),
                pipeline("hfp", "never"),
                pipeline("events", "snapshot"),
            ]
        },
    )
    answers["/api/v1/projects/helsinki/pipelines/registry/source-deletes"] = (
        200,
        report(lastComplete={**FRESH, "at": datetime.now(UTC).isoformat()}),
    )
    answers["/api/v1/projects/helsinki/pipelines/events/source-deletes"] = (
        503,
        {"detail": "try again"},
    )
    cases = check.run(base, "t0ken", ["helsinki"], timedelta(hours=48), 10)
    assert [name for name, _ in cases] == ["helsinki/registry", "helsinki/events"]
    assert cases[0][1] == []
    assert cases[1][1][0].startswith("the report could not be read")
    assert set(seen) == {"Bearer t0ken"}
    xml = check.junit(cases)
    assert 'tests="2" failures="1"' in xml and 'name="helsinki/events"' in xml


def test_no_snapshot_pipeline_anywhere_is_red(portal):
    base, answers, _ = portal
    answers["/api/v1/projects/bbsk/pipelines"] = (
        200,
        {"items": [pipeline("podujatia", "never")]},
    )
    cases = check.run(base, "t", ["bbsk"], timedelta(hours=48), 10)
    assert cases == [
        ("snapshot-pipelines", ["no snapshot pipeline in bbsk: nothing is proved"])
    ]

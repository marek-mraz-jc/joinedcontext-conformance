"""T-5107: the ZAP baseline's rules file, its sign-in helper and its dev-only guard, offline."""

import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import edge_session

# Ids that name a hole in our own answers: they may be scoped to an address, never ignored.
NEVER_IGNORED = {"10010", "10020", "10035", "10038", "10054", "10055", "10063", "10202", "90022"}
DEV_HOST = re.compile(r"^https://(\([a-z|]+\)|[a-z]+)\\\.dev\\\.joinedcontext\\\.com/")


def rules():
    for line in (HERE / "rules.tsv").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        yield line.split("\t", 2)


def test_every_rule_is_one_zap_reads_and_scoped_to_a_dev_host():
    seen = list(rules())
    assert seen
    for plugin, level, rest in seen:
        assert re.fullmatch(r"\d{5}", plugin), plugin
        assert level in {"IGNORE", "INFO", "WARN", "FAIL", "OUTOFSCOPE"}, plugin
        if level == "OUTOFSCOPE":
            re.compile(rest)
            assert DEV_HOST.match(rest), f"{plugin}: {rest} is not anchored to one dev host"
        else:
            assert rest.startswith("(") and rest.endswith(")"), f"{plugin} carries its reason"
            assert level != "IGNORE" or plugin not in NEVER_IGNORED, f"{plugin} may only be scoped"


def test_the_runner_refuses_any_domain_but_dev(tmp_path):
    run = subprocess.run(
        [str(HERE / "run.sh"), str(tmp_path)], env={"JC_ZAP_DOMAIN": "joinedcontext.com", "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 2 and "dev only" in run.stderr


def test_the_login_form_action_is_keycloaks():
    page = '<form id="other" action="/x"></form><form id="kc-form-login" action="https://idm/login?code=1&amp;t=2">'
    assert edge_session.form_action(page) == "https://idm/login?code=1&t=2"
    assert edge_session.form_action("<html>no form</html>") is None


@pytest.fixture
def realm():
    """A login page whose form posts over plain http, and a counter of what was posted."""
    posted = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f'<form id="kc-form-login" action="http://127.0.0.1:{self.server.server_port}/login">'.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posted.append(self.path)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/", posted
    server.shutdown()


def test_the_password_never_goes_over_plain_http(realm):
    portal, posted = realm
    with pytest.raises(SystemExit, match="plain http"):
        edge_session.edge_cookie(portal, "demo.viewer@hel.fi", "not-sent")
    assert posted == []

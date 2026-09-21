"""T-2586: the suite waits out a spent rate limit once, and only once (EP-20)."""

from __future__ import annotations

import http.server
import threading

import pytest

from rate_limit import RateLimitedSession


def _serve(answers: list[tuple[int, dict]]):
    """A local server answering `answers` in order, the last one repeating."""
    served: list[int] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - the stdlib names it
            status, headers = answers[min(len(served), len(answers) - 1)]
            served.append(status)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, served


@pytest.mark.parametrize("header", ["RateLimit-Reset", "Retry-After"])
def test_a_spent_limit_is_waited_out_and_the_request_repeated_once(header):
    server, served = _serve([(429, {header: "7"}), (200, {})])
    slept: list[float] = []
    try:
        session = RateLimitedSession(sleep=slept.append)
        response = session.post(f"http://127.0.0.1:{server.server_address[1]}/mcp", timeout=5)
    finally:
        server.shutdown()
    assert response.status_code == 200
    assert slept == [7.0]
    assert session.waited == 7.0
    assert served == [429, 200]


@pytest.mark.parametrize(
    "headers",
    [{}, {"RateLimit-Reset": "soon"}, {"RateLimit-Reset": "3600"}],
    ids=["no reset", "unreadable reset", "reset too far"],
)
def test_a_limit_with_no_window_to_wait_is_answered_as_it_came(headers):
    server, served = _serve([(429, headers), (200, {})])
    slept: list[float] = []
    try:
        response = RateLimitedSession(sleep=slept.append).post(
            f"http://127.0.0.1:{server.server_address[1]}/mcp", timeout=5
        )
    finally:
        server.shutdown()
    assert response.status_code == 429
    assert slept == []
    assert served == [429]


def test_a_second_429_is_not_retried_again():
    server, served = _serve([(429, {"RateLimit-Reset": "1"})])
    try:
        response = RateLimitedSession(sleep=lambda _: None).post(
            f"http://127.0.0.1:{server.server_address[1]}/mcp", timeout=5
        )
    finally:
        server.shutdown()
    assert response.status_code == 429
    assert served == [429, 429]

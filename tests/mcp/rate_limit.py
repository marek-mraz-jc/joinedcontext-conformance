"""A session that waits out an endpoint's spent rate limit once, instead of failing on it (T-2586).

The read-parity matrix asks every read over both doors of one endpoint, and a run reaches the
endpoint's own limit (EP-20) before its last cases. The limit is doing its job, so the suite
waits: on a `429` it sleeps for the seconds `RateLimit-Reset` (or `Retry-After`) names, retries
the request once, and says on stderr how long it waited. A second `429` is returned as it came,
and the limit is never raised or bypassed for a test.
"""

from __future__ import annotations

import sys
import time

import requests

# A reset further away than this is not a window to wait out inside a test run.
LONGEST_WAIT = 90.0


def _seconds(response: requests.Response) -> float | None:
    for header in ("RateLimit-Reset", "Retry-After"):
        value = response.headers.get(header)
        if value is None:
            continue
        try:
            return max(0.0, float(value))
        except ValueError:
            continue
    return None


class RateLimitedSession(requests.Session):
    def __init__(self, sleep=time.sleep) -> None:
        super().__init__()
        self._sleep = sleep
        self.waited = 0.0

    def request(self, method, url, *args, **kwargs):
        response = super().request(method, url, *args, **kwargs)
        if response.status_code != 429:
            return response
        wait = _seconds(response)
        if wait is None or wait > LONGEST_WAIT:
            return response
        print(f"rate limit spent at {url}: waited {wait:.0f} s, retrying once", file=sys.stderr)
        self._sleep(wait)
        self.waited += wait
        return super().request(method, url, *args, **kwargs)

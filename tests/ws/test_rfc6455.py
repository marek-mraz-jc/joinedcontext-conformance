"""RFC 6455 close handshake and protocol-error codes of the view channel (T-4879).

One case per row of `rfc6455_probe.py`; the probe skips the broker's own messages and judges
only control frames and the close.
"""

import pytest
from rfc6455_probe import CASES, probe

_results: dict[str, str] = {}


@pytest.mark.parametrize("case", list(CASES))
def test_rfc6455_case(ws_url, case):
    if not _results:
        _results.update(probe(ws_url))
    assert _results[case] == "PASS", f"Autobahn case {case}: {_results[case]}"

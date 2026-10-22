"""The Antares feed surface's rendered documents pass the validator (T-5202).

fixtures/antares/ holds the three documents of the output `vsetky` (Event and Article sources)
that crates/antares-feed/tests/feed.rs renders with FEED_FIXTURE_DIR set; refresh them from
there when the surface changes (tests/feed/README.md in Antares).
"""

from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import validate_feed as vf

DIR = HERE / "fixtures" / "antares"
BASE = "http://localhost:9090/x/views/city/feed/v1/vsetky"
FILES = {"atom": "atom.xml", "rss": "rss.xml", "json": "feed.json"}


def body(fmt: str) -> bytes:
    return (DIR / f"vsetky.{FILES[fmt]}").read_bytes()


def test_each_format_has_no_finding():
    for fmt, file in FILES.items():
        assert vf.validate(body(fmt), fmt, f"{BASE}/{file}", "sk") == [], fmt


def test_the_three_formats_list_the_same_items():
    assert vf.cross_check({fmt: body(fmt) for fmt in FILES}) == []

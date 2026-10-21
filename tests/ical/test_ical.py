"""iCalendar (RFC 5545) validator and the Event mapping of a view's feed (T-5044, EP-125, EP-126).

The fixture cases run with nothing configured (ci.yml): `fixtures/good.ics` passes, and each
`fixtures/broken-<rule>.ics` fails with exactly the rule its name names, so a check that stops biting
goes red here. The live cases read `ICAL_URL` (an Endpoint's `.../ical/v1/events.ics`) and, with
`ICAL_NGSILD_URL` (the same Endpoint's `.../ngsi-ld/v1`), hold the feed against the view's Events.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

import ical_check

FIXTURES = HERE / "fixtures"
BROKEN = sorted(FIXTURES.glob("broken-*.ics"))
EVENTS = json.loads((FIXTURES / "events.json").read_text(encoding="utf-8"))


def rules(findings) -> set[str]:
    return {f.rule for f in findings}


def test_the_good_feed_has_no_finding():
    data = (FIXTURES / "good.ics").read_bytes()
    assert ical_check.validate(data) == []
    assert len(ical_check.vevents(data)) == 3


def test_every_rule_has_a_broken_fixture():
    documented = {
        line.split()[0]
        for line in ical_check.__doc__.splitlines()
        if line.startswith("    ") and not line.strip().startswith("ical_check")
    }
    assert {p.stem.removeprefix("broken-") for p in BROKEN} == documented


@pytest.mark.parametrize("path", BROKEN, ids=lambda p: p.stem)
def test_a_broken_feed_fails_with_its_named_rule(path):
    expected = path.stem.removeprefix("broken-")
    assert rules(ical_check.validate(path.read_bytes())) == {expected}


def test_folding_keeps_a_multi_octet_character_whole():
    data = (FIXTURES / "good.ics").read_bytes()
    lines, _ = ical_check.content_lines(data)
    description = next(line for line in lines if line.startswith("DESCRIPTION:"))
    assert "Námestí SNP" in description, "unfolding lost a character"


def test_the_good_feed_maps_every_event_of_its_view():
    data = (FIXTURES / "good.ics").read_bytes()
    assert ical_check.mapping_findings(data, EVENTS) == []


@pytest.mark.parametrize(
    ("rule", "change"),
    [
        (
            "event-missing",
            lambda e: e.append(
                {
                    "id": "urn:ngsi-ld:Event:new",
                    "type": "Event",
                    "name": "Nové",
                    "startDate": "2026-12-01",
                }
            ),
        ),
        ("event-extra", lambda e: e.pop(1)),
        ("event-summary", lambda e: e[2].update(name="Iné divadlo")),
        ("event-location", lambda e: e[1].update(address="Radvaň")),
        ("event-url", lambda e: e[0].update(url="https://example.org/other")),
    ],
)
def test_a_feed_that_drops_or_bends_an_event_fails_with_its_rule(rule, change):
    entities = copy.deepcopy(EVENTS)
    change(entities)
    data = (FIXTURES / "good.ics").read_bytes()
    assert rules(ical_check.mapping_findings(data, entities)) == {rule}


def test_the_cli_exits_1_on_a_broken_file_and_0_on_the_good_one(capsys):
    assert ical_check.main([str(FIXTURES / "good.ics")]) == 0
    assert ical_check.main([str(FIXTURES / "broken-uid.ics")]) == 1
    assert "uid: VEVENT 2 without UID" in capsys.readouterr().out


@pytest.fixture(scope="module")
def live_feed() -> bytes:
    url = os.getenv("ICAL_URL", "").strip()
    if not url:
        pytest.skip(
            "ICAL_URL is not set: the live feed of an Endpoint, .../ical/v1/events.ics"
        )
    return ical_check.fetch(url)[0]


def test_the_live_feed_conforms_to_rfc_5545(live_feed):
    findings = ical_check.validate(live_feed)
    assert not findings, "\n".join(map(str, findings[:20]))


def test_the_live_feed_holds_every_event_of_its_view(live_feed):
    ngsild = os.getenv("ICAL_NGSILD_URL", "").strip()
    if not ngsild:
        pytest.skip("ICAL_NGSILD_URL is not set: the same Endpoint's .../ngsi-ld/v1")
    findings = ical_check.mapping_findings(live_feed, ical_check.view_events(ngsild))
    assert not findings, "\n".join(map(str, findings[:20]))

"""The matcher of the Helsinki live check (T-3907) on fixture frames and entities, no network."""

from __future__ import annotations

import json
import math

from live import Frame, Seen, epoch, frame_of, judge, metres, seen_of

T0 = "2026-10-12T08:00:00.000Z"
START = epoch(T0) - 1
END = START + 300
LAT, LON = 60.17, 24.94
# Metres per degree of latitude on the mean Earth radius.
PER_DEG = 2 * math.pi * 6371008.8 / 360


def tst(second: int) -> str:
    return f"2026-10-12T08:{second // 60:02d}:{second % 60:02d}.000Z"


def frame(second: int, vehicle: str = "22-7", spd: float | None = 8.5) -> Frame:
    return Frame(vehicle, tst(second), LAT, LON, spd, "2550", epoch(tst(second)) + 0.2)


def seen(second: int, vehicle: str = "22-7", north_m: float = 0.0, via: str = "ws", **kw) -> Seen:
    at = epoch(tst(second))
    fields = dict(vehicle=vehicle, observed=tst(second), lat=LAT + north_m / PER_DEG, lon=LON, speed=8.5,
                  unit="MTS", route="2550", modified=tst(second).replace(".000Z", ".400Z"),
                  received=at + 0.6, via=via)
    fields.update(kw)
    return Seen(**fields)


def lines(report) -> dict[str, bool]:
    return {name: ok for name, ok, _ in report.lines}


def run(frames, seen_, served=None):
    return judge(frames, seen_, served if served is not None else seen_[-1:], START, END)


def test_a_bus_that_follows_the_feed_is_green():
    frames = [frame(s) for s in range(0, 290)]
    r = run(frames, [seen(s) for s in range(0, 290)])
    assert r.ok, r.text()
    assert "p95 0.20 s" in r.text()


def test_49_m_passes_and_51_m_fails():
    assert 48.9 < metres(LAT, LON, LAT + 49 / PER_DEG, LON) < 49.1
    assert run([frame(1)], [seen(1, north_m=49)]).lines[0][:2] == ("position", True)
    assert run([frame(1)], [seen(1, north_m=51)]).lines[0][:2] == ("position", False)


def test_speed_in_another_unit_or_value_is_red():
    assert not lines(run([frame(1)], [seen(1, unit="KMH")]))["speed"]
    assert not lines(run([frame(1)], [seen(1, speed=30.6)]))["speed"]
    # A frame without speed: the entity carries none.
    assert lines(run([frame(1, spd=None)], [seen(1, speed=None, unit=None)]))["speed"]


def test_route_other_than_the_frame_is_red():
    assert not lines(run([frame(1)], [seen(1, route="550")]))["route"]


def test_a_served_bus_missing_from_hfp_is_red():
    r = run([frame(1)], [seen(1)], served=[seen(1), seen(2, vehicle="22-99")])
    assert not lines(r)["presence"]
    assert "22-99" in r.text() and not r.ok


def test_a_stale_entity_is_red():
    # Older than the slot ttl at the end of the run.
    old = seen(0, observed="2026-10-12T07:50:00.000Z")
    assert not lines(run([frame(1)], [seen(1)], served=[old]))["freshness"]
    # Behind the feed: served at second 1 while HFP had reached second 120 before the read.
    behind = seen(1, via="rest", received=epoch(tst(121)))
    r = run([frame(s) for s in range(1, 121)], [seen(s) for s in range(1, 121)], served=[behind])
    assert not lines(r)["freshness"], r.text()


def test_an_empty_feed_is_no_data_and_red():
    r = run([], [])
    assert not r.ok and r.lines[0][0] == "no data"
    r = run([frame(1)], [], served=[])
    assert not r.ok and "served no Vehicle" in r.text()


def test_missing_notifications_lower_completeness_and_latency_is_judged_on_the_socket():
    frames = [frame(s) for s in range(0, 200)]
    half = [seen(s) for s in range(0, 200, 2)]
    assert not lines(run(frames, half))["completeness"]
    slow = [seen(s, received=epoch(tst(s)) + 2.0) for s in range(0, 200)]
    assert not lines(run(frames, slow))["latency"]
    # No notification at all: a red latency line, never a pass.
    reads = [seen(s, via="rest") for s in range(0, 200)]
    assert not lines(run(frames, reads))["latency"]


def test_run_errors_are_red_lines():
    r = judge([frame(1)], [seen(1)], [seen(1)], START, END, ["read answered 401: unauthorized"])
    assert not r.ok and r.lines[0] == ("run", False, "read answered 401: unauthorized")


def test_parsers_take_hfp_and_ngsi_ld_and_skip_the_rest():
    vp = {"VP": {"oper": 22, "veh": 7, "lat": LAT, "long": LON, "spd": 8.5, "route": "2550", "tst": T0}}
    assert frame_of(json.dumps(vp), 1.0) == Frame("22-7", T0, LAT, LON, 8.5, "2550", 1.0)
    assert frame_of(json.dumps({"DOO": {}}), 1.0) is None
    assert frame_of(json.dumps({"VP": {**vp["VP"], "lat": None, "long": None}}), 1.0) is None
    assert frame_of(b"not json", 1.0) is None
    entity = {
        "id": "urn:ngsi-ld:Vehicle:hel.fi:helsinki:22-7", "type": "Vehicle",
        "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [LON, LAT]},
                     "observedAt": T0, "modifiedAt": "2026-10-12T08:00:00.400Z"},
        "https://example.org/speed": {"type": "Property", "value": 8.5, "unitCode": "MTS"},
        "route": {"type": "Property", "value": "2550"},
        "fleetVehicleId": {"type": "Property", "value": "22-7"},
    }
    s = seen_of(entity, 2.0, "ws")
    assert (s.vehicle, s.speed, s.unit, s.route, s.modified) == ("22-7", 8.5, "MTS", "2550", "2026-10-12T08:00:00.400Z")
    assert seen_of({"id": "x", "type": "Vehicle"}, 2.0, "ws") is None

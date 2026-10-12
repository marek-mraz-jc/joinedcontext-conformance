#!/usr/bin/env python3
"""The Helsinki live feed, end to end (T-3907, AP-147).

    live.py --endpoint https://dev.joinedcontext.com/api/endpoint/<slug> [--seconds 300] [--junit out.xml]

For `--seconds` it listens to the same HFP topics as the `hsl-hfp` DataSource (wss://mqtt.hsl.fi,
the public feed, read only), reads the endpoint's Vehicle entities every `--poll` seconds and
holds one WebSocket Subscription on the endpoint's `/ws`, then matches all three by bus and
by the frame's `tst`, which the pipeline writes as `observedAt`:

- correctness: position within 50 m, speed equal in m/s with unitCode MTS, route equal to the
  frame's route id, every served bus present in HFP and not stale;
- completeness: the share of an admitted bus's frames that reached the broker;
- latency per hop, p50/p95: HFP `tst` → the broker's `modifiedAt` → WebSocket receive.

The token (`HELSINKI_TOKEN`, a ServiceAccount's) goes in the Authorization header and nowhere
else; the script never writes an entity. Exit 0 when every line is green, 1 otherwise, and 1
for no data: an empty feed is never a green report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

# The fleet cache's silence window (deployment components/pipeline-runner/streams/resources.yaml):
# a bus silent this long loses its slot, and the reaper removes its entity.
TTL_S = 300.0
POSITION_M = 50.0
# A served position older than the bus's newest frame by more than this, at the moment of the
# read, is behind the feed (the pipeline writes every frame of an admitted bus, T-3906).
BEHIND_S = 30.0
# Frames this close to the end may still be on their way; completeness does not count them.
IN_FLIGHT_S = 10.0
COMPLETENESS = 0.95
WS_P95_S = 1.0
# The hsl-hfp DataSource's fixed list (T-3893): four bus trunk lines, every tram, metro, train, ferry.
TOPICS = [f"/hfp/v2/journey/ongoing/vp/bus/+/+/{route}/#" for route in ("2510", "2550", "4560", "4570")] + [
    f"/hfp/v2/journey/ongoing/vp/{mode}/#" for mode in ("tram", "metro", "train", "ferry")]


def epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance on the mean Earth radius."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def percentile(values: list[float], share: float) -> float:
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass(frozen=True)
class Frame:
    """One HFP position report, as the pipeline would read it."""

    vehicle: str
    tst: str
    lat: float
    lon: float
    spd: float | None
    route: str
    received: float


@dataclass(frozen=True)
class Seen:
    """One Vehicle as the endpoint served it: by a read (`rest`) or a notification (`ws`)."""

    vehicle: str
    observed: str
    lat: float
    lon: float
    speed: float | None
    unit: str | None
    route: str | None
    modified: str | None
    received: float
    via: str


def frame_of(payload: bytes | str, received: float) -> Frame | None:
    """The frame of an MQTT message, or None for another kind of frame or a report without a fix."""
    try:
        vp = json.loads(payload).get("VP")
    except (ValueError, AttributeError):
        return None
    if not isinstance(vp, dict) or not all(isinstance(vp.get(k), (int, float)) for k in ("lat", "long")):
        return None
    spd = vp.get("spd")
    return Frame(
        vehicle=f"{vp.get('oper')}-{vp.get('veh')}", tst=str(vp.get("tst")), lat=float(vp["lat"]),
        lon=float(vp["long"]), spd=float(spd) if isinstance(spd, (int, float)) else None,
        route=str(vp.get("route")), received=received,
    )


def _attr(entity: dict, name: str):
    """An attribute by its short name, or by an expanded IRI ending in it."""
    if name in entity:
        return entity[name]
    return next((v for k, v in entity.items() if k.endswith(("/" + name, "#" + name))), None)


def seen_of(entity: dict, received: float, via: str) -> Seen | None:
    """A served Vehicle, or None when it carries no fleet id or no located position."""
    fleet, location = _attr(entity, "fleetVehicleId"), _attr(entity, "location")
    if not isinstance(fleet, dict) or not isinstance(location, dict):
        return None
    try:
        lon, lat = location["value"]["coordinates"][:2]
        observed = location["observedAt"]
    except (KeyError, TypeError, ValueError):
        return None
    speed, route = _attr(entity, "speed"), _attr(entity, "route")
    return Seen(
        vehicle=str(fleet.get("value")), observed=observed, lat=float(lat), lon=float(lon),
        speed=speed.get("value") if isinstance(speed, dict) else None,
        unit=speed.get("unitCode") if isinstance(speed, dict) else None,
        route=str(route["value"]) if isinstance(route, dict) and "value" in route else None,
        modified=location.get("modifiedAt") or entity.get("modifiedAt"), received=received, via=via,
    )


@dataclass
class Report:
    lines: list[tuple[str, bool, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.lines.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return bool(self.lines) and all(ok for _, ok, _ in self.lines)

    def text(self) -> str:
        out = [f"{'PASS' if ok else 'FAIL'} {name}: {detail}" for name, ok, detail in self.lines]
        out.append("counts: " + ", ".join(f"{k} {v}" for k, v in self.counts.items()))
        return "\n".join(out)

    def junit(self) -> str:
        suite = ET.Element("testsuite", name="helsinki-live", tests=str(len(self.lines)),
                           failures=str(sum(not ok for _, ok, _ in self.lines)))
        for name, ok, detail in self.lines:
            case = ET.SubElement(suite, "testcase", classname="helsinki-live", name=name)
            if not ok:
                ET.SubElement(case, "failure", message=detail)
            else:
                ET.SubElement(case, "system-out").text = detail
        return ET.tostring(suite, encoding="unicode")


def _few(items: list[str]) -> str:
    return ", ".join(items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")


def judge(frames: list[Frame], seen: list[Seen], served: list[Seen], start: float, end: float,
          errors: list[str] = ()) -> Report:
    """The report of one run. `seen` holds every served position (reads and notifications),
    `served` the last read of the endpoint, `start`/`end` the run's window in epoch seconds."""
    r = Report()
    ws = [s for s in seen if s.via == "ws"]
    r.counts = {"hfp frames": len(frames), "hfp buses": len({f.vehicle for f in frames}),
                "served positions": len(seen), "notifications": len(ws), "served buses": len(served)}
    for error in errors:
        r.add("run", False, error)
    if not frames:
        r.add("no data", False, "the HFP feed delivered no position frame")
        return r
    if not served:
        r.add("no data", False, "the endpoint served no Vehicle")
        return r

    by_key = {(f.vehicle, f.tst): f for f in frames}
    pairs = [(s, by_key[(s.vehicle, s.observed)]) for s in seen if (s.vehicle, s.observed) in by_key]
    r.counts["compared"] = len(pairs)
    if not pairs:
        r.add("correctness", False, "no served position carries the tst of a frame heard in the run")
    else:
        far = [f"{s.vehicle}@{s.observed} {metres(s.lat, s.lon, f.lat, f.lon):.0f} m" for s, f in pairs
               if metres(s.lat, s.lon, f.lat, f.lon) > POSITION_M]
        r.add("position", not far, f"{len(pairs) - len(far)}/{len(pairs)} within {POSITION_M:.0f} m"
              + (f"; off: {_few(far)}" if far else ""))
        bad_speed = [f"{s.vehicle}@{s.observed} {s.speed} {s.unit} vs {f.spd}" for s, f in pairs
                     if (f.spd is None and s.speed is not None)
                     or (f.spd is not None and (s.unit != "MTS" or not isinstance(s.speed, (int, float))
                                                or abs(s.speed - f.spd) > 1e-9))]
        r.add("speed", not bad_speed, f"{len(pairs) - len(bad_speed)}/{len(pairs)} equal in m/s (MTS)"
              + (f"; differ: {_few(bad_speed)}" if bad_speed else ""))
        bad_route = [f"{s.vehicle} {s.route} vs {f.route}" for s, f in pairs if s.route != f.route]
        r.add("route", not bad_route, f"{len(pairs) - len(bad_route)}/{len(pairs)} equal to the frame's route"
              + (f"; differ: {_few(bad_route)}" if bad_route else ""))

    # Presence: every bus the endpoint serves is one HFP reports, and is not stale.
    heard: dict[str, list[Frame]] = {}
    for f in frames:
        heard.setdefault(f.vehicle, []).append(f)
    missing, stale = [], []
    for s in served:
        observed = epoch(s.observed)
        if observed < end - TTL_S:
            stale.append(f"{s.vehicle} observedAt {s.observed}, older than {TTL_S:.0f} s")
        elif s.vehicle not in heard:
            if observed >= start:
                missing.append(f"{s.vehicle} (observedAt {s.observed}, never in HFP during the run)")
        else:
            newest = max((f for f in heard[s.vehicle] if f.received <= s.received), key=lambda f: epoch(f.tst),
                         default=None)
            if newest and epoch(newest.tst) - observed > BEHIND_S:
                stale.append(f"{s.vehicle} served {s.observed}, HFP already at {newest.tst}")
    r.add("presence", not missing, f"{len(served) - len(missing)}/{len(served)} served buses heard in HFP"
          + (f"; missing: {_few(missing)}" if missing else ""))
    r.add("freshness", not stale, f"{len(served) - len(stale)}/{len(served)} served buses current"
          + (f"; stale: {_few(stale)}" if stale else ""))

    # Completeness: the frames of an admitted bus from its first served position on.
    reached = {(s.vehicle, s.observed) for s in (ws or seen)}
    admitted: dict[str, float] = {}
    for s in seen:
        admitted[s.vehicle] = min(admitted.get(s.vehicle, math.inf), epoch(s.observed))
    due = [f for f in frames if f.vehicle in admitted and epoch(f.tst) >= admitted[f.vehicle]
           and f.received <= end - IN_FLIGHT_S]
    got = sum((f.vehicle, f.tst) in reached for f in due)
    r.counts["admitted frames"], r.counts["reached"] = len(due), got
    share = got / len(due) if due else 0.0
    r.add("completeness", bool(due) and share >= COMPLETENESS,
          f"{got}/{len(due)} admitted frames reached the broker ({share:.1%}, "
          f"{'notifications' if ws else 'reads only'}), floor {COMPLETENESS:.0%}")

    # Latency per hop, from the notifications of frames heard in the run.
    timed = [(by_key[(s.vehicle, s.observed)], s) for s in ws if (s.vehicle, s.observed) in by_key]
    if not timed:
        r.add("latency", False, "the WebSocket delivered no notification of a frame heard in the run")
        return r
    total = [s.received - epoch(f.tst) for f, s in timed]
    hops = [(epoch(s.modified) - epoch(f.tst), s.received - epoch(s.modified)) for f, s in timed if s.modified]
    detail = f"tst→receive p50 {percentile(total, .5):.2f} s p95 {percentile(total, .95):.2f} s (n={len(total)})"
    if not hops:
        r.add("latency", False, detail + "; no notification carried modifiedAt, the hops are not measured")
        return r
    write, push = [h[0] for h in hops], [h[1] for h in hops]
    ws_p95 = percentile(push, .95)
    r.add("latency", ws_p95 < WS_P95_S,
          f"{detail}; tst→modifiedAt p50 {percentile(write, .5):.2f} s p95 {percentile(write, .95):.2f} s; "
          f"modifiedAt→receive p50 {percentile(push, .5):.2f} s p95 {ws_p95:.2f} s (bound {WS_P95_S:.0f} s)")
    return r


# --- the run --------------------------------------------------------------------------------


def listen_hfp(frames: list[Frame], errors: list[str], stop: threading.Event, topics: list[str]) -> None:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(transport="websockets", clean_session=True)
    client.ws_set_options(path="/")
    client.tls_set()
    client.on_connect = lambda c, *_: [c.subscribe(t, qos=0) for t in topics]
    client.on_message = lambda _c, _u, m: (f := frame_of(m.payload, time.time())) and frames.append(f)
    try:
        client.connect("mqtt.hsl.fi", 443, keepalive=30)
    except OSError as e:
        errors.append(f"HFP connect: {e}")
        return
    client.loop_start()
    stop.wait()
    client.loop_stop()
    client.disconnect()


def poll(url: str, token: str, every: float, seen: list[Seen], served: list[list[Seen]],
         errors: list[str], stop: threading.Event) -> None:
    import requests

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    while True:
        try:
            r = requests.get(f"{url}/ngsi-ld/v1/entities", headers=headers, timeout=20,
                             params={"type": "Vehicle", "limit": "1000", "options": "sysAttrs"})
            now = time.time()
            if r.status_code != 200:
                errors.append(f"read answered {r.status_code}: {r.text[:200]}")
            else:
                last = [s for s in (seen_of(e, now, "rest") for e in r.json()) if s]
                seen.extend(last)
                served[0] = last
        except (OSError, ValueError) as e:
            errors.append(f"read: {e}")
        if stop.wait(every):
            return


async def notifications(url: str, token: str, seen: list[Seen], errors: list[str], seconds: float) -> None:
    import asyncio

    from websockets.asyncio.client import connect

    ws_url = "ws" + url.removeprefix("http") + "/ws"
    subscription = {"type": "Subscription", "entities": [{"type": "Vehicle"}],
                    "notification": {"sysAttrs": True, "format": "normalized"}}
    deadline = time.monotonic() + seconds
    try:
        async with connect(ws_url, additional_headers={"Authorization": f"Bearer {token}"},
                           open_timeout=20, max_size=2**22) as ws:
            await ws.send(json.dumps({"metadata": {"x-op": "x-subscribe", "x-id": "live"}, "body": subscription}))
            while (left := deadline - time.monotonic()) > 0:
                try:
                    message = json.loads(await asyncio.wait_for(ws.recv(), left))
                except TimeoutError:
                    break
                now, meta = time.time(), message.get("metadata", {})
                if meta.get("x-op") == "x-result" and meta.get("x-status") != 201:
                    errors.append(f"subscribe answered {meta.get('x-status')}: {json.dumps(message.get('body'))[:200]}")
                    return
                if meta.get("x-op") == "x-notification":
                    seen.extend(s for s in (seen_of(e, now, "ws") for e in message["body"].get("data", [])) if s)
    except Exception as e:  # noqa: BLE001 (a refused upgrade, a closed socket: a red line, not a trace)
        errors.append(f"WebSocket: {type(e).__name__}: {str(e)[:200]}")


def main() -> int:
    import asyncio

    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--endpoint", default=os.getenv("HELSINKI_ENDPOINT_URL", ""),
                   help="the transport endpoint, https://…/api/endpoint/<slug>")
    p.add_argument("--seconds", type=float, default=float(os.getenv("HELSINKI_LIVE_SECONDS", "300")))
    p.add_argument("--poll", type=float, default=15.0)
    p.add_argument("--junit", default="")
    args = p.parse_args()
    token = os.getenv("HELSINKI_TOKEN", "")
    if not args.endpoint or not token:
        print("HELSINKI_ENDPOINT_URL and HELSINKI_TOKEN must be set (. tests/dev.env.sh helsinki-live)", file=sys.stderr)
        return 1
    url = args.endpoint.rstrip("/")
    frames: list[Frame] = []
    seen: list[Seen] = []
    served: list[list[Seen]] = [[]]
    errors: list[str] = []
    stop = threading.Event()
    start = time.time()
    workers = [threading.Thread(target=listen_hfp, args=(frames, errors, stop, TOPICS), daemon=True),
               threading.Thread(target=poll, args=(url, token, args.poll, seen, served, errors, stop), daemon=True)]
    for w in workers:
        w.start()
    asyncio.run(notifications(url, token, seen, errors, args.seconds))
    # The WebSocket ends at the deadline or on an error; the others run out the window either way.
    stop.wait(max(0.0, start + args.seconds - time.time()))
    stop.set()
    for w in workers:
        w.join(timeout=30)
    report = judge(list(frames), list(seen), served[0], start, time.time(), sorted(set(errors)))
    print(report.text())
    if args.junit:
        with open(args.junit, "w", encoding="utf-8") as out:
            out.write(report.junit())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())

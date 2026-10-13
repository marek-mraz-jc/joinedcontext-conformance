"""The 20-filter WebSocket smoke on dev's transport Endpoint (EP-115, ADR-N-049 §2.9, T-4133).

Ten anonymous sockets (EP-107 allows a caller ten) hold two filters each, one of every kind the
fan-out serves, for 60 s. Each filter must notify at least once and nothing outside it may
arrive. It is also the regression check for T-4131 (a socket held, no drop) and T-4132.

    python3 tests/ws/fanout_smoke.py wss://dev.joinedcontext.com/api/endpoint/<slug>/ws
    python3 tests/ws/fanout_smoke.py --selftest

Stdlib only (the conformance image carries no WebSocket package): the client of
tests/datasets/ws.py.
"""

from __future__ import annotations

import json
import math
import re
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "datasets"))
import ws  # noqa: E402

CENTRE = [24.9414, 60.1710]  # Helsinki railway station
FILTERS = {
    "type": {},
    "idPattern": {"entities": [{"type": "Vehicle", "idPattern": "urn:ngsi-ld:Vehicle:.*0$"}]},
    "q-speed": {"q": "speed>5"},
    "q-or": {"q": 'speed>5|transportMode=="train"'},
    "geoQ": {"geoQ": {"georel": "near;maxDistance==3000", "geometry": "Point", "coordinates": CENTRE}},
    "watched": {"watchedAttributes": ["speed"]},
    "attributes": {"notification": {"attributes": ["location", "speed"]}},
    "throttling": {"throttling": 10},
    "watched-q": {"watchedAttributes": ["location"], "q": "speed>=0"},
    "geo-attributes": {
        "geoQ": {"georel": "near;maxDistance==5000", "geometry": "Point", "coordinates": CENTRE},
        "notification": {"attributes": ["location"]},
    },
}


def value(entity: dict, name: str):
    attr = entity.get(name)
    return attr.get("value") if isinstance(attr, dict) else attr


def metres(a: list, b: list) -> float:
    """Haversine distance of two [lon, lat] points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def outside(kind: str, entity: dict) -> str | None:
    """Why `entity` should not have reached filter `kind`; None when it may."""
    if entity.get("type") != "Vehicle":
        return f"type {entity.get('type')}"
    speed = value(entity, "speed")
    if kind == "idPattern" and not re.fullmatch(r"urn:ngsi-ld:Vehicle:.*0", entity.get("id", "")):
        return f"id {entity.get('id')}"
    if kind == "q-speed" and not (isinstance(speed, (int, float)) and speed > 5):
        return f"speed {speed}"
    if kind == "q-or" and not ((isinstance(speed, (int, float)) and speed > 5) or value(entity, "transportMode") == "train"):
        return f"speed {speed}, mode {value(entity, 'transportMode')}"
    if kind in ("geoQ", "geo-attributes"):
        limit = 3000 if kind == "geoQ" else 5000
        point = (value(entity, "location") or {}).get("coordinates")
        # 1 % slack for the broker's own distance arithmetic.
        if not point or metres(point, CENTRE) > limit * 1.01:
            return f"location {point}"
    if kind == "attributes" and set(entity) - {"id", "type", "@context", "location", "speed"}:
        return f"attributes {sorted(entity)}"
    if kind == "geo-attributes" and set(entity) - {"id", "type", "@context", "location"}:
        return f"attributes {sorted(entity)}"
    return None


def hold(url: str, kinds: list[str], seconds: float, seen: dict, wrong: list, closed: list) -> None:
    """One socket with the filters `kinds`, listened to for `seconds`."""
    try:
        sock, status = ws.connect(url, 30)
    except OSError as error:
        closed.append(f"{kinds}: {error}")
        return
    with sock:
        if status != 101:
            closed.append(f"{kinds}: upgrade {status}")
            return
        for kind in kinds:
            body = {"type": "Subscription", "entities": [{"type": "Vehicle"}], **FILTERS[kind]}
            sock.sendall(ws.frame(json.dumps({"metadata": {"x-op": "x-subscribe", "x-id": kind}, "body": body})))
        names: dict[str, str] = {}
        sock.settimeout(2)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                opcode, payload = ws.read_message(sock)
            except (TimeoutError, socket.timeout):
                continue
            except (OSError, ValueError) as error:
                closed.append(f"{kinds}: {error}")
                return
            if opcode == 0x8:
                closed.append(f"{kinds}: closed by the server")
                return
            if opcode != 0x1:
                continue
            message = json.loads(payload)
            meta = message.get("metadata", {})
            if meta.get("x-op") == "x-result" and meta.get("x-id") in kinds:
                if meta.get("x-status") != 201:
                    wrong.append(f"{meta['x-id']}: subscribe answered {meta.get('x-status')}")
                else:
                    names[message["body"]["id"]] = meta["x-id"]
            elif meta.get("x-op") == "x-notification":
                kind = names.get(meta.get("x-subscription"))
                if kind is None:
                    wrong.append(f"a notification for an unknown subscription {meta.get('x-subscription')}")
                    continue
                seen[f"{kind}-messages"] = seen.get(f"{kind}-messages", 0) + 1
                for entity in message.get("body", {}).get("data", []):
                    seen[kind] = seen.get(kind, 0) + 1
                    why = outside(kind, entity)
                    if why:
                        wrong.append(f"{kind}: {entity.get('id')} outside the filter ({why})")
            elif meta.get("x-op") in ("x-gap", "x-closing"):
                wrong.append(f"{kinds}: {meta.get('x-op')} {meta.get('x-reason', '')}")


def run(url: str, seconds: float) -> int:
    kinds = list(FILTERS)
    pairs = [[kinds[i % len(kinds)], kinds[(i + 1) % len(kinds)]] for i in range(10)]
    seen: dict[str, int] = {}
    wrong: list[str] = []
    closed: list[str] = []
    threads = [threading.Thread(target=hold, args=(url, pair, seconds, seen, wrong, closed)) for pair in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    silent = [kind for kind in kinds if not seen.get(kind)]
    # Throttling 10 s on two sockets: one message per socket per window, two more at the
    # edges of the run (dev showed 6 per socket in 40 s).
    if seen.get("throttling-messages", 0) > 2 * (seconds // 10 + 3):
        wrong.append(f"throttling: {seen['throttling-messages']} messages in {seconds:.0f} s")
    print(json.dumps({"seconds": seconds, "entities and messages": seen, "silent": silent, "wrong": wrong[:20], "closed": closed}, indent=1))
    return 1 if silent or wrong or closed else 0


def selftest() -> None:
    near = {"id": "urn:ngsi-ld:Vehicle:10", "type": "Vehicle", "speed": {"type": "Property", "value": 7},
            "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [24.95, 60.17]}}}
    assert all(outside(kind, near) is None for kind in ("type", "idPattern", "q-speed", "q-or", "geoQ", "attributes"))
    far = {**near, "id": "urn:ngsi-ld:Vehicle:11", "speed": {"value": 3},
           "location": {"value": {"coordinates": [25.2, 60.3]}}, "transportMode": {"value": "bus"}}
    assert outside("idPattern", far) and outside("q-speed", far) and outside("q-or", far)
    assert outside("geoQ", far) and outside("attributes", far)
    assert outside("type", {"type": "Stop"})
    assert 1100 < metres([24.9414, 60.1710], [24.9614, 60.1710]) < 1120
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    elif len(sys.argv) in (2, 3):
        sys.exit(run(sys.argv[1], float(sys.argv[2]) if len(sys.argv) == 3 else 60))
    else:
        sys.exit(__doc__)

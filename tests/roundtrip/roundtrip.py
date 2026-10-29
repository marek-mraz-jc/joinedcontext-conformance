#!/usr/bin/env python3
"""Write here, read everywhere, on a live Endpoint (T-5914): the deployment-time half of T-5913.

Write mode (dev, needs a token): mints one test Entity `urn:ngsi-ld:AirQualityObserved:t-roundtrip-<run>`
through NGSI-LD, changes its pm10 once (two observedAt instances), then reads it back through every
surface the Endpoint serves and compares value, unitCode, observedAt and geometry:

  current   NGSI-LD retrieve and query, OData, OGC API Features item, CSV (OGC f=csv),
            SensorThings Thing + Location + Datastream, MCP get_entity
  temporal  NGSI-LD temporal, OGC {type}-history, SensorThings Observations
  stream    WebSocket notification (subscribed before the write), when the Endpoint serves it

then deletes the Entity (in `finally`, so a red read still cleans up) and checks NGSI-LD, OData and
OGC no longer return it. Leftovers of a killed run (the same id prefix) are deleted first. A surface
the Endpoint does not serve is a `not served` case (skipped in junit), never a silent pass.

Read mode (prod, anonymous, no write): `--read <entity id>` takes NGSI-LD's answer for that seeded
test Entity as the reference and compares every other served surface with it.

    roundtrip.py --endpoint URL [--token-env NGSILD_TOKEN] [--out DIR]       # write mode
    roundtrip.py --endpoint URL --read urn:ngsi-ld:AirQualityObserved:...     # read mode
    roundtrip.py --self-test                                                   # the comparison alone

URL is an Endpoint root: https://api.{domain}/api/endpoint/{slug} through the gateway, or
http://localhost:9090/x/views/{view} on a bare broker (selftest.sh). The token is read from the
environment variable --token-env names (the secrets mount sets it); it is never printed or written.
Writes `junit.xml` and `cases.tsv` into --out; exits 1 on any red case.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

TYPE = "AirQualityObserved"
PREFIX = f"urn:ngsi-ld:{TYPE}:t-roundtrip-"
UA = "joinedcontext-conformance roundtrip (T-5914)"
WAIT = float(os.environ.get("ROUNDTRIP_WAIT_SECS", "15"))
REASON = {"JC-Reason": "roundtrip%20test%20cleanup"}
q = lambda s: urllib.parse.quote(s, safe="")


class Http:
    def __init__(self, root: str, token: str = ""):
        self.root, self.token = root.rstrip("/"), token

    def call(self, method, path, body=None, accept="application/json", headers=None):
        """(status, parsed JSON or text or None); never raises on an HTTP or network error."""
        h = {"User-Agent": UA, "Accept": accept, **(headers or {})}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.root + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, raw, kind = r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            status, raw, kind = e.code, e.read(), e.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, str(e)
        text = raw.decode("utf-8", "replace")
        if "json" in kind and text:
            try:
                return status, json.loads(text)
            except ValueError:
                pass
        return status, text

    get = lambda self, path, **kw: self.call("GET", path, **kw)


# ---------------------------------------------------------------------------- what is expected


def expected_of(entity: dict, history: dict[str, list] | None = None) -> dict:
    """{current: {attr: {value, unitCode?, observedAt?}}, geometry, history: {attr: [(observedAt, value)]}}."""
    current, geometry = {}, None
    for k, a in entity.items():
        if not isinstance(a, dict):
            continue
        if a.get("type") == "GeoProperty" and k == "location":
            geometry = a.get("value")
        elif a.get("type") == "Property" and isinstance(a.get("value"), (str, int, float, bool)):
            current[k] = {m: a[m] for m in ("value", "unitCode", "observedAt") if m in a}
    return {"current": current, "geometry": geometry, "history": history or {}}


def same(want, got) -> bool:
    """A plain value as a surface gives it; CSV gives text, a time may carry milliseconds."""
    if got is None:
        return False
    if isinstance(want, bool) or isinstance(got, bool):
        return str(want).lower() == str(got).lower()
    if isinstance(want, (int, float)):
        try:
            return float(got) == float(want)
        except (TypeError, ValueError):
            return False
    return instant(want) == instant(got) if is_time(want) else str(got) == str(want)


def is_time(v) -> bool:
    return isinstance(v, str) and len(v) >= 20 and v[10] == "T" and v.endswith("Z")


def instant(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return v


def diff(where: str, want: dict, got: dict, members=("value",)) -> list[str]:
    """Differences of one reader's {attr: {member: x}} against the expected attributes."""
    out = []
    for attr, w in want.items():
        g = got.get(attr)
        for m in members:
            if m not in w:
                continue
            gv = None if g is None else g.get(m)
            if not same(w[m], gv):
                out.append(f"{where}: {attr}.{m}: want {w[m]!r} got {'nothing' if gv is None else repr(gv)}")
    return out


def diff_geometry(where, want, got) -> list[str]:
    if want is None or got == want:
        return []
    return [f"{where}: geometry: want {json.dumps(want)} got {json.dumps(got) if got else 'nothing'}"]


def diff_history(where, want: list, got: list) -> list[str]:
    w = sorted((instant(t), float(v)) for t, v in want)
    g = sorted((instant(t), float(v)) for t, v in got if isinstance(v, (int, float)))
    return [] if w == g else [f"{where}: instances: want {[(str(t), v) for t, v in w]} got {[(str(t), v) for t, v in g]}"]


# ---------------------------------------------------------------------------- the readers
# Each reader answers ("not served", why) | (differences); `served` names the surfaces.


def plain_of(row: dict) -> dict:
    return {k: {"value": v} for k, v in row.items() if isinstance(v, (str, int, float, bool))}


def read_ngsild(http, eid, exp):
    s, body = http.get(f"/ngsi-ld/v1/entities/{q(eid)}")
    if s != 200 or not isinstance(body, dict):
        return [f"NGSI-LD retrieve: HTTP {s}"]
    got = {k: v for k, v in body.items() if isinstance(v, dict)}
    out = diff("NGSI-LD retrieve", exp["current"], got, ("value", "unitCode", "observedAt"))
    return out + diff_geometry("NGSI-LD retrieve", exp["geometry"], (body.get("location") or {}).get("value"))


def read_ngsild_query(http, eid, exp):
    s, body = http.get(f"/ngsi-ld/v1/entities?{urllib.parse.urlencode({'type': TYPE, 'id': eid})}")
    if s != 200 or not isinstance(body, list):
        return [f"NGSI-LD query: HTTP {s}"]
    if [e.get("id") for e in body] != [eid]:
        return [f"NGSI-LD query: want [{eid}] got {[e.get('id') for e in body]}"]
    return diff("NGSI-LD query", exp["current"], body[0], ("value", "unitCode", "observedAt"))


def read_odata(http, eid, exp):
    flt = urllib.parse.urlencode({"$filter": f"id eq '{eid}'"}, quote_via=urllib.parse.quote)
    s, body = http.get(f"/odata/v4/{TYPE}?{flt}")
    if s != 200 or not isinstance(body, dict):
        return [f"OData: HTTP {s}"]
    rows = [r for r in body.get("value", []) if r.get("id") == eid]
    if len(rows) != 1:
        return [f"OData: want one row {eid}, got {len(rows)}"]
    return diff("OData", exp["current"], plain_of(rows[0])) + diff_geometry("OData", exp["geometry"], rows[0].get("location"))


def read_ogc(http, eid, exp):
    s, body = http.get(f"/ogc/features/collections/{TYPE}/items/{q(eid)}", accept="application/geo+json")
    if s != 200 or not isinstance(body, dict):
        return [f"OGC item: HTTP {s}"]
    out = diff("OGC item", exp["current"], plain_of(body.get("properties") or {}))
    return out + diff_geometry("OGC item", exp["geometry"], body.get("geometry"))


def read_csv(http, eid, exp):
    flt = urllib.parse.urlencode({"f": "csv", "filter": f"id='{eid}'", "filter-lang": "cql2-text"},
                                 quote_via=urllib.parse.quote)
    s, text = http.get(f"/ogc/features/collections/{TYPE}/items?{flt}", accept="text/csv")
    if s != 200 or not isinstance(text, str):
        return [f"CSV: HTTP {s}"]
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("id") == eid]
    if len(rows) != 1:
        return [f"CSV: want one row {eid}, got {len(rows)}"]
    return diff("CSV", exp["current"], {k: {"value": v} for k, v in rows[0].items() if v != ""})


def read_sta(http, eid, exp):
    s, thing = http.get(f"/sta/v1.1/Things('{q(eid)}')?$expand=Locations,Datastreams")
    if s != 200 or not isinstance(thing, dict):
        return [f"STA Thing: HTTP {s}"]
    got = plain_of(thing.get("properties") or {})
    out = []
    for d in thing.get("Datastreams") or []:
        name = d.get("name")
        if name not in exp["current"]:
            continue
        unit = (d.get("unitOfMeasurement") or {}).get("name")
        ds, obs = http.get(f"/sta/v1.1/Datastreams('{q(d['@iot.id'])}')/Observations?$orderby=phenomenonTime%20desc&$top=1")
        last = (obs.get("value") or [{}])[0] if ds == 200 and isinstance(obs, dict) else {}
        got[name] = {"value": last.get("result"), "unitCode": unit, "observedAt": last.get("phenomenonTime")}
    out += diff("STA", exp["current"], got, ("value", "unitCode", "observedAt"))
    loc = next((x.get("location") for x in thing.get("Locations") or []), None)
    return out + diff_geometry("STA Location", exp["geometry"], loc)


def read_mcp(http, eid, exp):
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_entity", "arguments": {"id": eid}}}
    s, body = http.call("POST", "/mcp", call, accept="application/json, text/event-stream")
    entity = (((body or {}).get("result") or {}).get("structuredContent") or {}).get("entity") if isinstance(body, dict) else None
    if s != 200 or not isinstance(entity, dict):
        return [f"MCP get_entity: HTTP {s} {str(body)[:200]}"]
    return diff("MCP", exp["current"], entity, ("value", "unitCode", "observedAt"))


def read_temporal(http, eid, exp):
    out = []
    for attr, want in exp["history"].items():
        s, body = http.get(f"/ngsi-ld/v1/temporal/entities/{q(eid)}?attrs={q(attr)}")
        if s != 200 or not isinstance(body, dict):
            out.append(f"NGSI-LD temporal: HTTP {s}")
            continue
        inst = body.get(attr) or []
        inst = inst if isinstance(inst, list) else [inst]
        out += diff_history(f"NGSI-LD temporal {attr}", want, [(i.get("observedAt"), i.get("value")) for i in inst])
    return out


def read_ogc_history(http, eid, exp):
    flt = urllib.parse.urlencode({"filter": f"entityId='{eid}'", "filter-lang": "cql2-text", "limit": 100},
                                 quote_via=urllib.parse.quote)
    s, body = http.get(f"/ogc/features/collections/{TYPE}-history/items?{flt}", accept="application/geo+json")
    if s != 200 or not isinstance(body, dict):
        return [f"OGC history: HTTP {s}"]
    rows = [f.get("properties") or {} for f in body.get("features", [])]
    return [d for attr, want in exp["history"].items()
            for d in diff_history(f"OGC history {attr}", want,
                                  [(r.get("validFrom"), r.get(attr)) for r in rows if r.get(attr) is not None])]


def read_sta_observations(http, eid, exp):
    out = []
    for attr, want in exp["history"].items():
        s, body = http.get(f"/sta/v1.1/Datastreams('{q(eid + '/' + attr)}')/Observations?$top=100")
        if s != 200 or not isinstance(body, dict):
            out.append(f"STA Observations {attr}: HTTP {s}")
            continue
        out += diff_history(f"STA Observations {attr}", want,
                            [(o.get("phenomenonTime"), o.get("result")) for o in body.get("value", [])])
    return out


CURRENT = [("ngsi-ld", "NGSI-LD retrieve", read_ngsild), ("ngsi-ld", "NGSI-LD query", read_ngsild_query),
           ("odata", "OData", read_odata), ("ogc", "OGC Features item", read_ogc), ("ogc", "CSV", read_csv),
           ("sta", "SensorThings Thing", read_sta), ("mcp", "MCP get_entity", read_mcp)]
TEMPORAL = [("ngsi-ld", "NGSI-LD temporal", read_temporal), ("ogc-history", "OGC history", read_ogc_history),
            ("sta", "SensorThings Observations", read_sta_observations)]


def served(http) -> dict[str, bool]:
    """Which surfaces the Endpoint serves, by its own roots (as tests/standards/run.sh probes them)."""
    ping = {"jsonrpc": "2.0", "id": 0, "method": "ping"}
    s_ogc, cols = http.get("/ogc/features/collections")
    ids = {c.get("id") for c in (cols.get("collections") or [])} if isinstance(cols, dict) else set()
    return {
        "ngsi-ld": http.get("/ngsi-ld/v1/types")[0] == 200,
        "odata": http.get("/odata/v4")[0] == 200,
        "ogc": s_ogc == 200,
        "ogc-history": f"{TYPE}-history" in ids,
        "sta": http.get("/sta/v1.1")[0] == 200,
        "mcp": http.call("POST", "/mcp", ping, accept="application/json, text/event-stream")[0] == 200,
        "ws": http.get("/ws")[0] in (400, 426),
    }


# ---------------------------------------------------------------------------- the stream reader


async def ws_listen(url: str, token: str, eid: str, ready: asyncio.Event, want: float) -> list[str]:
    """Subscribe to `eid` over the Endpoint's WebSocket; differences once a notification carries `want`.

    A refused upgrade, a closed connection or a timeout is a red case of its own, never a traceback
    that ends the run before the junit is written."""
    from websockets.exceptions import WebSocketException

    try:
        return await _ws_listen(url, token, eid, ready, want)
    except (WebSocketException, OSError, TimeoutError, ValueError) as e:
        return [f"WebSocket: {type(e).__name__}: {e}"]


async def _ws_listen(url: str, token: str, eid: str, ready: asyncio.Event, want: float) -> list[str]:
    from websockets.asyncio.client import connect

    headers = {"User-Agent": UA, **({"Authorization": f"Bearer {token}"} if token else {})}
    async with connect(url, subprotocols=["ngsi-ld-ws"], additional_headers=headers, open_timeout=WAIT) as ws:
        await asyncio.wait_for(ws.recv(), WAIT)  # x-welcome
        sub = {"metadata": {"x-op": "x-subscribe", "x-id": "rt"},
               "body": {"type": "Subscription", "entities": [{"id": eid, "type": TYPE}]}}
        await ws.send(json.dumps(sub))
        seen = []
        deadline = time.monotonic() + 4 * WAIT
        while time.monotonic() < deadline:
            m = json.loads(await asyncio.wait_for(ws.recv(), deadline - time.monotonic()))
            md = m.get("metadata", {})
            if md.get("x-op") == "x-result" and md.get("x-id") == "rt":
                if md.get("x-status") != 201:
                    return [f"WebSocket subscribe: x-status {md.get('x-status')}"]
                ready.set()
            elif md.get("x-op") == "x-notification":
                for e in (m.get("body") or {}).get("data", []):
                    v = (e.get("pm10") or {}).get("value") if isinstance(e.get("pm10"), dict) else e.get("pm10")
                    seen.append(v)
                    if e.get("id") == eid and same(want, v):
                        return []
        return [f"WebSocket: no notification of {eid} with pm10 {want} in {4 * WAIT:.0f} s (saw {seen})"]


# ---------------------------------------------------------------------------- the run


class Report:
    def __init__(self):
        self.cases: list[tuple[str, str, str]] = []  # (name, pass | fail | not served, detail)

    def add(self, name, result):
        if isinstance(result, tuple):
            self.cases.append((name, "not served", result[1]))
        else:
            self.cases.append((name, "fail" if result else "pass", "; ".join(result)))
        _, status, detail = self.cases[-1]
        print(f"  {'ok' if status == 'pass' else 'FAIL' if status == 'fail' else '--'}  {name}{': ' + detail if detail else ''}")

    def check(self, name, surface, srv, fn, *args):
        if not srv.get(surface):
            return self.add(name, ("not served", f"the Endpoint does not serve {surface}"))
        try:
            self.add(name, fn(*args))
        except Exception as e:  # a reader's crash is that reader's red case, the run goes on
            self.add(name, [f"{type(e).__name__}: {e}"])

    @property
    def red(self):
        return any(s == "fail" for _, s, _ in self.cases)

    def write(self, out: Path, mode: str):
        out.mkdir(parents=True, exist_ok=True)
        with (out / "cases.tsv").open("w") as f:
            f.write("case\tresult\tdetail\n")
            for c in self.cases:
                f.write("\t".join(x.replace("\t", " ").replace("\n", " ") for x in c) + "\n")
        suite = ET.Element("testsuite", name=f"roundtrip {mode}", tests=str(len(self.cases)),
                           failures=str(sum(s == "fail" for _, s, _ in self.cases)))
        for name, status, detail in self.cases:
            case = ET.SubElement(suite, "testcase", classname=f"roundtrip.{mode}", name=name)
            if status == "fail":
                ET.SubElement(case, "failure", message=detail[:500]).text = detail
            elif status == "not served":
                ET.SubElement(case, "skipped", message=detail)
        ET.ElementTree(suite).write(out / "junit.xml", encoding="utf-8", xml_declaration=True)


def reads(http, srv, eid, exp, rep: Report):
    for surface, name, fn in CURRENT:
        rep.check(f"current {name}", surface, srv, fn, http, eid, exp)
    for surface, name, fn in TEMPORAL:
        if exp["history"]:
            rep.check(f"temporal {name}", surface, srv, fn, http, eid, exp)


def sweep(http):
    """Delete what a killed run left: every Entity of the test prefix (the space holds only test data)."""
    s, body = http.get(f"/ngsi-ld/v1/entities?{urllib.parse.urlencode({'type': TYPE, 'idPattern': '^' + PREFIX, 'limit': 100})}")
    for e in body if s == 200 and isinstance(body, list) else []:
        if e.get("id", "").startswith(PREFIX):
            http.call("DELETE", f"/ngsi-ld/v1/entities/{q(e['id'])}", headers=REASON)


def entity(eid, t1):
    return {"id": eid, "type": TYPE,
            # The conformance space's model types name as a LanguageProperty (DM-86 refuses a Property).
            "name": {"type": "LanguageProperty", "languageMap": {"sk": "t-roundtrip sonda", "en": "t-roundtrip probe"}},
            "dateObserved": {"type": "Property", "value": t1},
            "pm10": {"type": "Property", "value": 18.4, "unitCode": "GQ", "observedAt": t1},
            "location": {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [19.1462, 48.7359]}}}


def write_run(http, srv, token, rep: Report, ws_url: str):
    run = datetime.now(UTC).strftime("%Y%m%dt%H%M%S") + secrets.token_hex(3)
    eid = PREFIX + run
    now = datetime.now(UTC).replace(microsecond=0)
    t1, t2 = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z"), (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    sweep(http)
    print(f"roundtrip write {eid}")

    async def go():
        listener, ready = None, asyncio.Event()
        if srv["ws"]:
            listener = asyncio.create_task(ws_listen(ws_url, token, eid, ready, 21.5))
            done, _ = await asyncio.wait([listener, asyncio.create_task(ready.wait())], timeout=WAIT,
                                         return_when=asyncio.FIRST_COMPLETED)
            if listener in done:  # the subscription failed before the write
                rep.add("stream WebSocket notification", listener.result() or ["WebSocket closed early"])
                listener = None
        try:
            s, b = await asyncio.to_thread(http.call, "POST", "/ngsi-ld/v1/entities", entity(eid, t1))
            rep.add("write NGSI-LD create", [] if s == 201 else [f"HTTP {s} {str(b)[:300]}"])
            if s != 201:
                return
            upd = {"pm10": {"type": "Property", "value": 21.5, "unitCode": "GQ", "observedAt": t2}}
            s, b = await asyncio.to_thread(http.call, "PATCH", f"/ngsi-ld/v1/entities/{q(eid)}/attrs", upd)
            rep.add("write NGSI-LD update", [] if s == 204 else [f"HTTP {s} {str(b)[:300]}"])
            if listener:
                try:
                    rep.add("stream WebSocket notification", await asyncio.wait_for(listener, 5 * WAIT))
                except (TimeoutError, OSError, ValueError) as e:
                    rep.add("stream WebSocket notification", [f"{type(e).__name__}: {e}"])
                listener = None
            elif not srv["ws"]:
                rep.add("stream WebSocket notification", ("not served", "the Endpoint does not serve ws"))
            current = {**entity(eid, t1), "pm10": upd["pm10"]}
            exp = expected_of(current, {"pm10": [(t1, 18.4), (t2, 21.5)]})
            await asyncio.to_thread(reads, http, srv, eid, exp, rep)
        finally:
            if listener:
                listener.cancel()
            s, _ = await asyncio.to_thread(http.call, "DELETE", f"/ngsi-ld/v1/entities/{q(eid)}", None, "application/json", REASON)
            rep.add("teardown NGSI-LD delete", [] if s in (204, 404) else [f"HTTP {s}"])

    asyncio.run(go())
    gone = []
    if http.get(f"/ngsi-ld/v1/entities/{q(eid)}")[0] != 404:
        gone.append("NGSI-LD still returns it")
    if srv["odata"]:
        s, b = http.get(f"/odata/v4/{TYPE}?" + urllib.parse.urlencode({"$filter": f"id eq '{eid}'"}, quote_via=urllib.parse.quote))
        if s != 200 or (isinstance(b, dict) and b.get("value")):
            gone.append(f"OData HTTP {s}, {len(b.get('value', [])) if isinstance(b, dict) else '?'} rows")
    if srv["ogc"] and http.get(f"/ogc/features/collections/{TYPE}/items/{q(eid)}")[0] != 404:
        gone.append("OGC item still answers")
    rep.add("teardown deleted everywhere", gone)


def read_run(http, srv, eid, rep: Report):
    """Prod: NGSI-LD's answer is the reference; no write, no history expectation beyond NGSI-LD's own."""
    print(f"roundtrip read {eid}")
    s, body = http.get(f"/ngsi-ld/v1/entities/{q(eid)}")
    if s != 200 or not isinstance(body, dict):
        rep.add("reference NGSI-LD retrieve", [f"HTTP {s}: the seeded test Entity is not there"])
        return
    history = {}
    for attr, a in body.items():
        if isinstance(a, dict) and a.get("observedAt") and isinstance(a.get("value"), (int, float)):
            _, tb = http.get(f"/ngsi-ld/v1/temporal/entities/{q(eid)}?attrs={q(attr)}&lastN=1")
            inst = (tb.get(attr) if isinstance(tb, dict) else None) or []
            history[attr] = [(i.get("observedAt"), i.get("value")) for i in (inst if isinstance(inst, list) else [inst])]
    exp = expected_of(body)
    rep.add("reference NGSI-LD retrieve", [])
    reads(http, srv, eid, exp, rep)
    # temporal readers: the newest instance NGSI-LD temporal gives must be the current value
    for attr, inst in history.items():
        want = exp["current"][attr]
        rep.add(f"temporal NGSI-LD lastN {attr}",
                [] if inst and same(want["value"], inst[-1][1]) and same(want["observedAt"], inst[-1][0])
                else [f"want ({want['observedAt']}, {want['value']}) got {inst}"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--endpoint", help="Endpoint root URL")
    p.add_argument("--read", metavar="ENTITY_ID", help="read mode: compare every surface with NGSI-LD for this seeded Entity")
    p.add_argument("--token-env", default="NGSILD_TOKEN", help="environment variable holding the bearer token (write mode)")
    p.add_argument("--out", type=Path, default=Path("roundtrip-report"))
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.endpoint or not a.endpoint.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        p.error("--endpoint is an https URL (or http://localhost for the selftest broker)")
    token = os.environ.get(a.token_env, "").strip() if not a.read else ""
    local = a.endpoint.startswith(("http://localhost", "http://127.0.0.1"))
    if not a.read and not token and not local:
        p.error(f"write mode needs the bearer token in ${a.token_env} (the conformance ServiceAccount's, from the secrets mount)")
    if a.read and not a.read.startswith("urn:ngsi-ld:"):
        p.error("--read takes an NGSI-LD URN")
    if a.read and os.environ.get(a.token_env):
        print(f"read mode is anonymous: {a.token_env} is ignored", file=sys.stderr)
    http = Http(a.endpoint, token)
    srv = served(http)
    print("served: " + ", ".join(k for k, v in srv.items() if v))
    rep = Report()
    if not srv["ngsi-ld"]:
        rep.add("NGSI-LD root", ["the Endpoint answers no /ngsi-ld/v1/types: nothing to write or read through"])
    elif a.read:
        read_run(http, srv, a.read, rep)
    else:
        ws_url = a.endpoint.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"
        write_run(http, srv, token, rep, ws_url)
    rep.write(a.out, "read" if a.read else "write")
    print(f"roundtrip: {'RED' if rep.red else 'GREEN'} ({sum(s == 'pass' for _, s, _ in rep.cases)} ok, "
          f"{sum(s == 'fail' for _, s, _ in rep.cases)} red, {sum(s == 'not served' for _, s, _ in rep.cases)} not served)")
    return 1 if rep.red else 0


def self_test() -> int:
    exp = expected_of(entity("urn:x", "2026-10-28T10:00:00Z"), {"pm10": [("2026-10-28T10:00:00Z", 18.4)]})
    assert exp["current"]["pm10"] == {"value": 18.4, "unitCode": "GQ", "observedAt": "2026-10-28T10:00:00Z"}
    assert exp["geometry"]["type"] == "Point" and "location" not in exp["current"]
    full = ("value", "unitCode", "observedAt")
    assert not diff("x", exp["current"], {"pm10": {"value": "18.4"}, "name": {"value": "t-roundtrip probe"},
                                          "dateObserved": {"value": "2026-10-28T10:00:00.000Z"}})
    assert diff("x", exp["current"], {"pm10": {"value": 18.38}}), "a rounded float is red"
    assert diff("x", exp["current"], {"pm10": {"value": 18.4, "observedAt": "2026-10-28T10:00:00Z"}}, full), "a dropped unitCode is red"
    assert diff_geometry("x", exp["geometry"], None) and not diff_geometry("x", exp["geometry"], exp["geometry"])
    assert not diff_history("x", [("2026-10-28T10:00:00Z", 18.4)], [("2026-10-28T10:00:00.000Z", 18.4)])
    assert diff_history("x", [("2026-10-28T10:00:00Z", 18.4), ("2026-10-28T10:01:00Z", 21.5)], [("2026-10-28T10:01:00Z", 21.5)])
    rep = Report()
    rep.add("a", [])
    rep.add("b", ("not served", "no"))
    assert not rep.red
    rep.add("c", ["bad"])
    assert rep.red
    # a WebSocket that refuses the connection is a red case, not a traceback
    gone = asyncio.run(ws_listen("ws://127.0.0.1:9/ws", "", "urn:x", asyncio.Event(), 1.0))
    assert gone and gone[0].startswith("WebSocket: "), gone
    print("roundtrip.py self-test: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

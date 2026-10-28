# Roundtrip: write here, read everywhere (T-5914)

`roundtrip.py` proves on a live Endpoint what Antares' in-process matrix (T-5913,
`crates/antares-broker/tests/write_read_matrix.rs`) proves in the broker: a value written through
NGSI-LD reads back unchanged through every surface the Endpoint serves, gateway, edge and Policies
included.

## 1. Write mode (dev)

```bash
NGSILD_TOKEN=<conformance service account token> \
  python3 tests/roundtrip/roundtrip.py --endpoint https://api.dev.joinedcontext.com/api/endpoint/<slug> --out report/
```

It creates one Entity `urn:ngsi-ld:AirQualityObserved:t-roundtrip-<run>`, updates its `pm10` once
(two `observedAt` instances), and compares value, `unitCode`, `observedAt` and geometry through:

| Kind | Readers |
|---|---|
| write | NGSI-LD create, NGSI-LD update |
| current | NGSI-LD retrieve and query, OData, OGC API Features item, CSV (OGC `f=csv`), SensorThings Thing + Location + Datastream, MCP `get_entity` |
| temporal | NGSI-LD temporal, OGC `{type}-history`, SensorThings Observations |
| stream | WebSocket notification, subscribed before the write |
| teardown | delete; NGSI-LD, OData and OGC no longer return it |

The delete runs in `finally`, so a red read still removes the Entity; a run killed before it is
cleaned by the next run, which first deletes every Entity of the `t-roundtrip-` prefix. A surface
the Endpoint does not serve is a `not served` case (skipped in junit), never a pass.

On dev the target is the `conformance` Endpoint of the banskabystrica project (deployment
`components/context-gateway/seed/banskabystrica/endpoint-conformance.yaml`): a space of its own
that nothing publishes, written only by the `conformance` ServiceAccount (Policy
`conformance-write`). The release gate runs it as stage `roundtrip` (deployment
`scripts/standards-gate.py`).

## 2. Read mode (prod)

```bash
python3 tests/roundtrip/roundtrip.py --endpoint https://api.smartcity.joinedcontext.com/api/endpoint/<id> \
  --read urn:ngsi-ld:<Type>:<seeded test entity>
```

Anonymous, no write: NGSI-LD's answer for the seeded Entity is the reference, every other served
surface must agree with it, and NGSI-LD temporal `lastN=1` must give the current value of each
observed attribute. The prod nightly runs it when `ROUNDTRIP_ENDPOINT` and `ROUNDTRIP_ENTITY` are
set (`tests/prod-nightly/run.sh`, suite `roundtrip`).

## 3. Self-test

- `python3 tests/roundtrip/roundtrip.py --self-test`: the comparison alone (fast CI).
- `tests/roundtrip/selftest.sh`: the suite against a local broker, the Antares image dev runs with a
  memory store and every surface dev mounts, one view over `broker/schema.json`. Write mode and read
  mode go green with no surface `not served`; then `broker/rounding_proxy.py` in front of the broker
  rounds OData's `pm10` to an integer and the run must go red on `current OData` alone.

Output: `cases.tsv` (case, result, detail) and `junit.xml` in `--out`; exit 1 on any red case.

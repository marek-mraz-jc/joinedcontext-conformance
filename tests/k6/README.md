# Performance budgets (TS-05, TS-22, EP-05, EP-20, EP-28, EP-41, EP-44, OPS-18)

Automated k6 benchmark scenarios asserting gateway and endpoint performance budgets:
P99 overhead ≤ 5.0 ms on cached policy evaluations, ≥ 5 000 req/s per replica, zero memory leaks
over a 30-minute stress soak, and high-volume multi-format streaming export limits.

Tasks:
- **T-0065**: Latency and throughput benchmark (`gateway-latency-load.js`)
- **T-0066**: Endurance and memory soak benchmark (`gateway-endurance.js`)
- **T-0067**: Bulk export streaming benchmark (`bulk-export.js`)
- **T-0332**: Sustained load on one endpoint's representations (`transport_endpoint_load.js`)

## Execution

Benchmarks execute via the `jc-conformance k6` command or `./run.sh`, selecting the target script
with `JC_K6_SCRIPT`:

```bash
# T-0065: Latency and throughput (default)
GATEWAY_URL=https://host/cs/ovzdusie/ngsi-ld/v1 \
BROKER_URL=http://broker:9090/ngsi-ld/v1 \
jc-conformance k6

# T-0066: Endurance and memory soak
JC_K6_SCRIPT=gateway-endurance.js \
GATEWAY_URL=https://host/cs/ovzdusie/ngsi-ld/v1 \
GATEWAY_METRICS_URL=http://gateway:9090/metrics \
jc-conformance k6

# T-0067: Bulk export streaming
JC_K6_SCRIPT=bulk-export.js \
EXPORT_URL=https://host/api/endpoint/ovzdusie-pub \
jc-conformance k6

# T-0332: Sustained load on one endpoint
JC_K6_SCRIPT=transport_endpoint_load.js \
ENDPOINT_URL=https://host/api/endpoint/{slug} \
BROKER_URL=http://broker:9090/ngsi-ld/v1 \
JC_K6_BURST_RATE=1000 \
jc-conformance k6
```

---

## 1. Gateway Latency & Throughput (`gateway-latency-load.js`, T-0065)

EP-28 budgets the gateway *overhead*, not the total read latency. Two scenarios run side by side:
one sustains `JC_K6_RATE` requests per second against the space surface, while the other reads the
same entity through the gateway and directly from the broker listener, calculating the paired overhead
difference under load. Without `BROKER_URL`, overhead cannot be paired and only throughput and ingress
latency are asserted.

### Budget Table

| Threshold | Requirement |
|---|---|
| `ep28_gateway_overhead_ms p(99)<5` | EP-28 — p99 overhead ≤ 5.0 ms for cached policy evaluations |
| `ops18_gateway_read_ms p(95)<50` | OPS-18 — p95 ingress latency ≤ 50 ms for cached entity reads |
| `ts22_gateway_reads count>=95% of rate×duration` | TS-22 — sustained throughput (5 000 req/s target) |
| `dropped_iterations count<1` | target arrival rate delivered without dropped iterations |
| `http_req_failed rate<0.001` | error rate under 0.1% |
| `checks rate==1.0` | every read answers 200 with the requested entity payload |

### Environment Variables

| Variable | Meaning |
|---|---|
| `GATEWAY_URL` | NGSI-LD API root of the space under test |
| `BROKER_URL` | broker internal listener; without it EP-28 overhead is not measured |
| `BROKER_TENANT` | `NGSILD-Tenant` header for the direct internal hop |
| `GATEWAY_TOKEN` | bearer token for the space surface |
| `ENTITY_ID` | entity to read; discovered from `GET /entities?limit=1` when unset |
| `ENTITY_TYPE` | narrows entity discovery when `ENTITY_ID` is unset |
| `JC_K6_RATE` | arrival rate, default 5000 req/s |
| `JC_K6_DURATION` | duration of sustained load, default `1m` |
| `JC_K6_VUS` | pre-allocated virtual users, default 50 |
| `JC_K6_OVERHEAD_RATE` | paired-read overhead sampling rate, default 20/s |
| `JC_K6_WARMUP_READS` | warm-up reads to populate policy cache, default 20 |

---

## 2. Gateway Endurance & Memory Soak (`gateway-endurance.js`, T-0066)

TS-22 mandates zero memory leaks during a sustained 30-minute stress test. One scenario sustains
continuous read load against the space surface while an asynchronous probe scrapes Prometheus text
metrics (`process_resident_memory_bytes` or `container_memory_working_set_bytes`) at regular intervals.
At the end of the run, linear regression calculates the RSS slope percentage per minute over the
configured evaluation window (`JC_K6_MEMORY_WINDOW`) following warmup (`JC_K6_WARMUP`). If the slope
exceeds `JC_K6_MEMORY_SLOPE` %/min, `ts22_memory_growth` is triggered.

The 30-minute soak belongs on dedicated hardware; running it on shared CI runners or desktop machines produces noisy timing and memory metrics. The self-test (`selftest.py`) proves the gate, mathematical slope calculations, and probe wiring against local in-process stubs in seconds.

### Budget Table

| Threshold | Requirement |
|---|---|
| `ts22_memory_growth count<1` | TS-22 — zero memory leaks detected over evaluation window |
| `ops18_gateway_read_ms p(95)<50` | OPS-18 — p95 read latency remains under 50 ms throughout soak |
| `ts22_gateway_reads count>=95% of rate×duration` | TS-22 — sustained request completion rate |
| `dropped_iterations count<1` | zero dropped arrival iterations during soak |
| `http_req_failed rate<0.0001` | zero read drops/failures allowed (error rate < 0.01%) |
| `checks rate==1.0` | 100% check pass rate on entity payloads |

### Environment Variables

| Variable | Meaning |
|---|---|
| `GATEWAY_URL` | NGSI-LD API root of the space under test |
| `GATEWAY_METRICS_URL` | Prometheus `/metrics` endpoint exposing gateway resident memory |
| `BROKER_METRICS_URL` | Prometheus `/metrics` endpoint for broker process memory (optional) |
| `GATEWAY_TOKEN` | bearer token for the space surface |
| `ENTITY_ID` | entity to read; discovered from `GET /entities?limit=1` when unset |
| `ENTITY_TYPE` | narrows entity discovery |
| `JC_K6_RATE` | sustained read rate, default 500 req/s |
| `JC_K6_DURATION` | soak duration, default `30m` |
| `JC_K6_VUS` | pre-allocated virtual users, default 50 |
| `JC_K6_WARMUP_READS` | initial cache warm-up reads, default 20 |
| `JC_K6_PROBE_INTERVAL` | interval between Prometheus scrapes, default `30s` |
| `JC_K6_WARMUP` | initial stabilization period ignored by slope calculation, default `5m` |
| `JC_K6_MEMORY_WINDOW` | duration from end of test evaluated for RSS slope, default `20m` |
| `JC_K6_MEMORY_SLOPE` | maximum permissible RSS slope (%/min), default `0.05` |

---

## 3. Bulk Export Streaming (`bulk-export.js`, T-0067)

Validates high-volume streaming exports across tabular (`file.csv`, EP-45) and archive bundle
(`file.zip`, EP-41) representations under concurrent streams (EP-44, OPS-18), asserting time to first
byte (TTFB), total export completion time, zero timeouts, and complete payload integrity.

### Budget Table

| Threshold | Requirement |
|---|---|
| `ep44_export_ttfb_ms p(95)<1000` | EP-44 — time to first byte under 1.0 second on streaming exports |
| `ep44_export_ms max<300000` | EP-44 — total export duration completes under 300 seconds |
| `ep44_export_timeouts count<1` | EP-44 — zero HTTP 504 / gateway read timeouts |
| `ep41_export_rows count>=rows*streams` | EP-41 — full dataset streamed without row truncation |
| `http_req_failed rate<0.001` | zero dropped HTTP connections |
| `checks rate==1.0` | 100% check pass rate on format headers, CSV columns, and ZIP magic |

### Environment Variables

| Variable | Meaning |
|---|---|
| `EXPORT_URL` | Endpoint base URL, e.g. `https://host/api/endpoint/{slug}` (required) |
| `EXPORT_ROWS` | minimum expected rows per export stream, default 50 000 |
| `JC_K6_EXPORT_STREAMS` | concurrent streams per format, default 3 |
| `GATEWAY_TOKEN` | bearer token for authorized endpoint access |

---

## 4. Endpoint Sustained Load (`transport_endpoint_load.js`, T-0332)

Holds `JC_K6_RATE` requests per second against one Endpoint's `ngsi-ld/v1/entities` while a
second scenario downloads its `file.geojson`, and afterwards offers a burst the endpoint's own
rate limit has to shed.

**What the 5 ms is.** The task this script answers asks for a p99 under 5.0 ms. That number is
EP-28's budget on the gateway's **overhead**, so the run pairs each measured read against the
broker's internal listener and asserts the difference; without `BROKER_URL` the difference
cannot be measured and the budget is not evaluated. A p99 on the total read would be a budget
on the broker, the network and the size of the dataset rather than on anything the gateway
decides, so the total read keeps the OPS-18 ingress budget instead, and the download is
budgeted on its first byte.

**What the burst is for.** A load run that stays under the limit proves nothing about the
limit. With `JC_K6_BURST_RATE` a phase after the sustained one deliberately exceeds it and
`ep20_limit_shed` must count at least one 429; without it the same counter must stay at zero,
because nothing under the limit may ever be shed. `dropped connections` in the task's sense are
asserted on the sustained phase only: the burst's 429s are the point rather than a failure.

### Budget Table

| Threshold | Requirement |
|---|---|
| `ep28_endpoint_overhead_ms p(99)<5` | EP-28 — p99 gateway overhead ≤ 5.0 ms on the endpoint surface |
| `ts05_endpoint_read_ms p(95)<50` | OPS-18 — p95 ingress latency ≤ 50 ms for a cached read |
| `ep05_geojson_ttfb_ms p(95)<1000` | EP-05 — the download answers its first byte within a second |
| `ts05_endpoint_reads count>=95% of rate×duration` | TS-05 — the offered rate was actually served |
| `dropped_iterations count<1` | the arrival rate was delivered without dropped iterations |
| `http_req_failed{phase:sustained} rate<0.0001` | error rate under 0.01% while under the limit |
| `ep20_limit_headers_missing count<1` | EP-20 — every answer names the limit (`RateLimit-*`) |
| `ep20_limit_shed count>=1` / `count<1` | EP-20 — a burst over the limit is shed; nothing under it is |
| `checks rate==1.0` | every read answered 200 with the entity asked for, every download a FeatureCollection |

### Environment Variables

| Variable | Meaning |
|---|---|
| `ENDPOINT_URL` | Endpoint base URL, e.g. `https://host/api/endpoint/{slug}` (required) |
| `BROKER_URL` | broker internal listener; without it the EP-28 overhead is not measured |
| `BROKER_TENANT` | `NGSILD-Tenant` header for the direct internal hop |
| `GATEWAY_TOKEN` | bearer token; omit it on a public endpoint |
| `ENTITY_TYPE` | entity type to read; discovered from `GET /types` when unset |
| `ENTITY_ID` | entity to read; discovered from `GET /entities?type=…&limit=1` when unset |
| `JC_K6_RATE` | sustained arrival rate, default 100 req/s |
| `JC_K6_DURATION` | duration of the sustained phase, default `1m` |
| `JC_K6_VUS` | pre-allocated virtual users, default 20 |
| `JC_K6_GEOJSON_RATE` | download rate running beside the reads, default 5/s |
| `JC_K6_OVERHEAD_RATE` | paired-read overhead sampling rate, default 10/s |
| `JC_K6_BURST_RATE` | burst rate after the sustained phase; unset leaves EP-20 unproven |
| `JC_K6_BURST_DURATION` | how long to hold the burst, default `10s` |
| `JC_K6_WARMUP_READS` | warm-up reads to populate the policy cache, default 20 |

---

## Self-test

`selftest.py` executes without dedicated hardware or network access in the conformance container:

```bash
python3 tests/k6/selftest.py
```

The self-test runs eight distinct validation suites:
1. `selftest.js (good)`: synthetic distribution inside every latency budget (must pass).
2. `selftest.js (bad)`: distribution exceeding every latency budget (must fail, asserting each threshold).
3. `gateway-latency-load.js`: live benchmark execution with `--no-thresholds` against an in-process stub space asserting paired hop reads and metric emission.
4. `selftest.js (endurance & export)`: proves the endurance budgets and bulk export budgets against conforming and failing distributions (RSS regression slope calculated via `slopePercent`).
5. `gateway-endurance.js`: wiring runs against local stubs with flat RSS (confirming count 0 on `ts22_memory_growth`) and climbing RSS (confirming `ts22_memory_growth >= 1`).
6. `bulk-export.js`: wiring run against streaming CSV and ZIP stubs asserting TTFB, row counting, and check passing.
7. `selftest.js (endpoint)`: proves the endpoint budgets against conforming and failing distributions.
8. `transport_endpoint_load.js`: wiring run against a stub endpoint that carries its own token bucket, asserting type and entity discovery, the paired hop, the GeoJSON download, and that a burst four hundred a second past a sixty a second bucket is shed while nothing under the limit is.

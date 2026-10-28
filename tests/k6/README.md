# Performance budgets (TS-05, TS-22, EP-05, EP-20, EP-28, EP-41, EP-44, OPS-18)

Automated k6 benchmark scenarios asserting gateway and endpoint performance budgets:
P99 overhead ≤ 5.0 ms on cached policy evaluations, ≥ 5 000 req/s per replica, zero memory leaks
over a 30-minute stress soak, and high-volume multi-format streaming export limits.

Tasks:
- **T-0065**: Latency and throughput benchmark (`gateway-latency-load.js`)
- **T-0066**: Endurance and memory soak benchmark (`gateway-endurance.js`)
- **T-0067**: Bulk export streaming benchmark (`bulk-export.js`)
- **T-0332**: Sustained load on one endpoint's representations (`transport_endpoint_load.js`)
- **T-2800**: The nightly dev budget, 100 entities at 20 requests a second, p95 under 300 ms, on an endpoint and its space's canonical surface (`dev-budgets.js`, run and judged by `scripts/budgets-dev.py`)
- **T-4889**: The platform's read surfaces at night on dev: the Portal grid's page, count and filter and an MCP tools/call through one Endpoint (`platform-surfaces.js`), 200 readers of the Portal's live view and 500 MQTT devices at 1 msg/s (`streams.py`, k6 holds neither stream), with `kubectl top` beside it (`night.sh`). The broker's own surfaces (NGSI-LD, OData, OGC, STA, iCal, MCP, WebSocket fan-out) are Antares `dev/perf/surfaces.sh` and its weekly `load-surfaces` lane
- **T-4903**: Slow neighbours: 5 loops of a slow read (default `/attributes`) beside a quick probe (`entities?limit=1`, 2/s); the probe's p95 stays under 300 ms and no slow read outlives the gateway's 10 s deadline plus margin (`slow-neighbours.js`; `JC_K6_SLOW_PATH`, `JC_K6_PROBE_PATH`, `JC_K6_DURATION`)
- **T-5124**: Fair share: a pipeline's batch writes (`WRITE_TOKEN`) and a signed-in steward's reads (`STEWARD_TOKEN`) run for a baseline minute, then beside two minutes of anonymous reads at 5x the normal rate; each class's p95 under the flood stays within 20 % of its baseline and every anonymous answer is a 200 or a 429 with `Retry-After` (`fair-share.js`; `JC_K6_WRITE_TYPE`, `JC_K6_NORMAL_RATE`, `JC_K6_FLOOD_RATE`, `JC_K6_TOLERANCE`; run.sh exits 1 on a FAIL)
- **T-5042**: Policy and Endpoint budgets (ADR-N-057 section 4): every representation of one Endpoint (`/access`, NGSI-LD, OGC Features, OData, CSV, MCP; AuthZEN `/access/v1/evaluations` of 100 items with `JC_K6_EVALUATIONS=1`) at 1, 10 and 50 clients, server time and broker share from the gateway's `Server-Timing`; `/access` p95 server time under 20 ms, evaluations under 30 ms, and with `RESIDUAL_URL` (an Endpoint whose Policy adds `idPattern`, `q` and `geoQ` over the same data) its NGSI-LD and OGC reads within 10 % of the open Endpoint's; a gateway without `Server-Timing` fails as unmeasured (`policy-budgets.js`; `JC_K6_TYPE`, `JC_K6_LEVELS`, `JC_K6_STAGE`, `JC_K6_THINK_MS`; run.sh exits 1 on a FAIL). From outside the cluster the dev edge allows 300 requests a minute: `JC_K6_LEVELS=1 JC_K6_THINK_MS=450`
- **T-5901**: Prod at 1,000 req/s: the API mix with harder query shapes per standard (`make_requests.py`: NGSI-LD nested q, pick/omit, join, polygon within/intersects, temporal aggregation; OGC bbox, CQL2, sortby with 1000-row pages; SensorThings $expand/$filter/$orderby; OData $filter/$select/$apply/$expand and a JSON $batch; MCP with q; filtered CSV; iCal; CKAN), each a kind `<standard>.<query>` reported per kind and per standard; four profiles (`JC_K6_PROFILE`: `steady` 1000/s 10 min, `closed` 1000 VUs 10 min, `knee` 100 -> 1500/s over 15 min, `soak` 500/s 30 min) with the T-4754 abort (p95 > 2 s or errors > 2 % for a minute); `load.sh` records a run under `evidence/load/<date>-<target>-<profile>/` (k6 summary, table, `run.json` with the conformance and deployment shas) and remote-writes the metrics to Prometheus for the deployment's Grafana dashboard "k6 load runs" (`k6-load.json`); `weekly.sh` is the prod deployer's Sunday 05:00Z run (refuses 01:00-04:00Z, `kubectl top nodes` before and after, stops k6 when Postgres stays above 5 cores for a minute). The committed request list is `lists/prod.tsv`
- **T-3059**: The knowledge assistant under 20 concurrent public chats, every chat answered, p95 of a full answer recorded, RSS read with `kubectl top pod` against the 1 GB budget; bounded to `JC_K6_CHATS` chats (default 40, at most 200) because every chat spends the deployment's model budget (`assistant-chats.js`)

## Execution

Benchmarks execute via the `jc-conformance k6` command or `./run.sh`, selecting the target script
with `JC_K6_SCRIPT`:

```bash
# T-3059: 20 concurrent public chats on the assistant (spends model budget; at most 200 chats)
JC_K6_SCRIPT=assistant-chats.js \
JC_ASSISTANT_URL=https://assistant.dev.joinedcontext.com JC_ASSISTANT_PUBLIC_ID=helsinki-public JC_K6_CHATS=40 \
jc-conformance k6

# T-4714: distinct requests over NGSI-LD, OData, OGC, MCP, schema, CSV and CKAN at 100/s for a
# minute, with WebSocket subscribers held open (the list: kind, method, url[, probed status])
JC_K6_SCRIPT=api-mix-100rps.js JC_K6_REQUESTS=requests-mix-dev.tsv JC_K6_RATE=100 JC_K6_DURATION=1m \
jc-conformance k6

# T-4754: every public dataset of a catalogue asked on every surface (NGSI-LD page and count,
# geo near its own entity, temporal last week, OData, OGC, CSV, an MCP tools/call, schema, CKAN),
# probed once, anonymous, paced under the owner's limit (50/min, 500 per run)
python3 make_requests.py https://opendata.smartcity.joinedcontext.com > requests-prod.tsv
# then the ramp: 50/s rising to 500/s over 5 minutes, 500/s held 10 minutes, p95 under 500 ms per
# kind; the run aborts by itself after a minute of p95 over 2 s or errors over 2 %
JC_K6_SCRIPT=api-mix-100rps.js JC_K6_REQUESTS=requests-prod.tsv JC_K6_RAMP=50:500:5m \
JC_K6_DURATION=10m JC_K6_P95_MS=500 jc-conformance k6

# T-5901: one recorded run per profile, metrics remote-written through a port-forward; the
# loadtest ServiceAccount's token from a file (never on the command line)
JC_K6_PROFILE=steady JC_K6_REQUESTS=lists/prod.tsv JC_LOAD_TARGET=prod JC_DEPLOYMENT_SHA=<sha> \
JC_K6_TOKEN_FILE=/path/to/token K6_PROMETHEUS_RW_SERVER_URL=http://127.0.0.1:19090/api/v1/write ./load.sh
# the weekly prod run (cron `0 5 * * 0` on the prod deployer's host, announced first)
KUBECONFIG=... JC_PROM_NAMESPACE=<instance>-prometheus JC_K6_REQUESTS=lists/prod.tsv JC_LOAD_TARGET=prod \
JC_DEPLOYMENT_SHA=<sha> JC_K6_TOKEN_FILE=... ./weekly.sh

# T-4903: five slow read loops beside a quick probe on one Endpoint, two minutes
JC_K6_SCRIPT=slow-neighbours.js ENDPOINT_URL=https://api.host/api/endpoint/{slug} \
JC_K6_PROBE_PATH='/ngsi-ld/v1/entities?type=KeyPerformanceIndicator&limit=1' jc-conformance k6

# T-5042: every representation at 1, 10 and 50 clients, 30 s each, with the residual comparison
JC_K6_SCRIPT=policy-budgets.js ENDPOINT_URL=https://api.host/api/endpoint/{slug} \
RESIDUAL_URL=https://api.host/api/endpoint/{narrowed-slug} JC_K6_TYPE=Vehicle jc-conformance k6

# T-5124: an anonymous flood (100/s from 64 source addresses) beside a pipeline and a steward;
# the tokens come from the environment, never from a file in the repository
JC_K6_SCRIPT=fair-share.js ENDPOINT_URL=https://api.host/api/endpoint/{slug} \
WRITE_URL=https://host/cs/{space}/ngsi-ld/v1 JC_K6_WRITE_TYPE=FairShareProbe \
WRITE_TOKEN=... STEWARD_TOKEN=... jc-conformance k6 -- --local-ips=127.0.0.10-127.0.0.73

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

Validates high-volume streaming of the Endpoint's one file, `file.csv` (EP-44, EP-45; `file.zip` was
removed by T-4597), under concurrent streams (EP-44, OPS-18), asserting time to first
byte (TTFB), total export completion time, zero timeouts, and complete payload integrity.

### Budget Table

| Threshold | Requirement |
|---|---|
| `ep44_export_ttfb_ms p(95)<1000` | EP-44 — time to first byte under 1.0 second on streaming exports |
| `ep44_export_ms max<300000` | EP-44 — total export duration completes under 300 seconds |
| `ep44_export_timeouts count<1` | EP-44 — zero HTTP 504 / gateway read timeouts |
| `ep41_export_rows count>=rows*streams` | EP-41 — full dataset streamed without row truncation |
| `http_req_failed rate<0.001` | zero dropped HTTP connections |
| `checks rate==1.0` | 100% check pass rate on the CSV Content-Type, its id column and its row count |

### Environment Variables

| Variable | Meaning |
|---|---|
| `EXPORT_URL` | Endpoint base URL, e.g. `https://host/api/endpoint/{slug}` (required) |
| `EXPORT_ROWS` | minimum expected rows per export stream, default 50 000 |
| `JC_K6_EXPORT_STREAMS` | concurrent CSV streams, default 3 |
| `GATEWAY_TOKEN` | bearer token for authorized endpoint access |

---

## 4. Endpoint Sustained Load (`transport_endpoint_load.js`, T-0332)

Holds `JC_K6_RATE` requests per second against one Endpoint's `ngsi-ld/v1/entities` while a
second scenario downloads its `file.csv` (its one file since T-4597), and afterwards offers a burst the endpoint's own
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
| `ep05_csv_ttfb_ms p(95)<1000` | EP-05 — the download answers its first byte within a second |
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
| `JC_K6_CSV_RATE` | download rate running beside the reads, default 5/s |
| `JC_K6_OVERHEAD_RATE` | paired-read overhead sampling rate, default 10/s |
| `JC_K6_BURST_RATE` | burst rate after the sustained phase; unset leaves EP-20 unproven |
| `JC_K6_BURST_DURATION` | how long to hold the burst, default `10s` |
| `JC_K6_WARMUP_READS` | warm-up reads to populate the policy cache, default 20 |

---

## 5. Platform surfaces at night (`night.sh`, `platform-surfaces.js`, `streams.py`, T-4889)

The deployer's batch runs `night.sh` against dev between 01:00 and 04:00Z (the script refuses other
hours). It never runs the ETSI TTF suite, keeps the edge's bot limits on and reads with a viewer token;
MQTT publishes with a device identity of a scratch space. Prod gets read-only shapes only, through
T-4877, and only after a green dev night.

| Shape | Load | Budget | Why this budget |
|---|---|---|---|
| `grid-page`, `grid-count`, `grid-filter` | 5 req/s each, 5 min | p95 < 800 ms, p99 < 2 s, errors < 1 % | the grid redraws on each page; above ~1 s a person sees it stall |
| `mcp` (tools/call query_entities) | 5 req/s, 5 min | the same | an agent's tool call sits inside a model turn the person waits on, so it gets the grid's budget |
| `sse` (Portal live view) | 200 readers held 5 min | every reader answered 200, none dropped, p95 to headers < 2 s | a dropped stream freezes a dashboard without telling anyone |
| `mqtt` (edge broker into a pipeline) | 500 devices x 1 msg/s, QoS 1 | 99.9 % acked, p95 PUBACK < 500 ms, 99 % of devices landed in the space | a device that loses acks retries and doubles the load; landed proves the pipeline, not only the broker |

`night.sh` writes `result.txt` (one PASS/FAIL/NOT RUN line per check), the k6 summaries and `top.txt`
(`kubectl top pod` every 30 s for the broker, gateway, Portal and PostgreSQL pods) under
`reports/night-<stamp>/`. `test_streams.py` proves `streams.py` fails a dropped stream, a refused
reader and a planted slow PUBACK.

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
6. `bulk-export.js`: wiring run against a streaming CSV stub asserting TTFB, row counting, and check passing.
7. `selftest.js (endpoint)`: proves the endpoint budgets against conforming and failing distributions.
8. `transport_endpoint_load.js`: wiring run against a stub endpoint that carries its own token bucket, asserting type and entity discovery, the paired hop, the CSV download, and that a burst four hundred a second past a sixty a second bucket is shed while nothing under the limit is.

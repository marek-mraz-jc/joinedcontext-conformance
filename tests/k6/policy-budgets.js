// Policy and Endpoint budgets (T-5042, ADR-N-057 section 4): every representation of one Endpoint
// at 1, 10 and 50 concurrent clients, one stage after the other, each answer's server time read
// from the gateway's `Server-Timing` (total, and the broker's share), the round trip beside it.
// Budgets: `/access` p95 server time < 20 ms; `/access/v1/evaluations` of 100 items p95 < 30 ms
// (JC_K6_EVALUATIONS=1, once the AuthZEN API serves it); with RESIDUAL_URL, an Endpoint over the
// same data whose Policy adds idPattern, q and geoQ, its NGSI-LD and OGC reads within 10 % of the
// open Endpoint's. A kind the Endpoint does not serve (setup's probe is not a 200) is left out
// and named. Against dev only, never the full ETS; production only in the T-4877 night window.
// The dev edge allows 300 requests a minute per address: from outside the cluster pace with
// JC_K6_THINK_MS and JC_K6_LEVELS=1, the full matrix runs from inside it.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { fairShare, serverTiming, summarize, timingTable } from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');

const ENDPOINT_URL = stripSlash(__ENV.ENDPOINT_URL);
const RESIDUAL_URL = stripSlash(__ENV.RESIDUAL_URL);
const TYPE = __ENV.JC_K6_TYPE || 'Vehicle';
const LEVELS = (__ENV.JC_K6_LEVELS || '1,10,50').split(',').map(Number).filter((n) => n > 0);
const STAGE = __ENV.JC_K6_STAGE || '30s';
const THINK_MS = Number(__ENV.JC_K6_THINK_MS || 0);
const EVALUATIONS = __ENV.JC_K6_EVALUATIONS === '1';
const ACCESS_P95_MS = Number(__ENV.JC_K6_ACCESS_P95_MS || 20);
const EVALUATIONS_P95_MS = Number(__ENV.JC_K6_EVALUATIONS_P95_MS || 30);
const RESIDUAL_TOLERANCE = Number(__ENV.JC_K6_RESIDUAL_TOLERANCE || 0.1);

const mcpCall = JSON.stringify({
  jsonrpc: '2.0',
  id: 1,
  method: 'tools/call',
  params: { name: 'query_entities', arguments: { type: [TYPE], limit: 10 } },
});
const evaluations = JSON.stringify({
  subject: { type: 'role', id: 'public' },
  evaluations: Array.from({ length: 100 }, (_, n) => ({
    action: { name: 'queryEntity' },
    resource: { type: TYPE, id: `urn:ngsi-ld:${TYPE}:k6-${n}` },
  })),
});
const JSON_HEADERS = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' };

// kind -> [method, path, body]
const KINDS = {
  access: ['GET', '/access'],
  ngsi: ['GET', `/ngsi-ld/v1/entities?type=${TYPE}&limit=10`],
  ogc: ['GET', `/ogc/features/collections/${TYPE}/items?limit=10`],
  odata: ['GET', `/odata/v4/${TYPE}?$top=10`],
  csv: ['GET', `/file.csv?type=${TYPE}`],
  mcp: ['POST', '/mcp', mcpCall],
  ...(EVALUATIONS ? { evaluations: ['POST', '/access/v1/evaluations', evaluations] } : {}),
};
const RESIDUAL_KINDS = ['ngsi', 'ogc'];

const serverMs = new Trend('t5042_server_ms', true);
const brokerMs = new Trend('t5042_broker_ms', true);
const roundTripMs = new Trend('t5042_round_trip_ms', true);
const unmeasured = new Counter('t5042_unmeasured');
// The residual comparison: one Trend per (kind, Endpoint), all levels together.
const compared = {};
for (const kind of RESIDUAL_KINDS) {
  compared[`${kind}_open`] = new Trend(`t5042_${kind}_open_ms`, true);
  compared[`${kind}_residual`] = new Trend(`t5042_${kind}_residual_ms`, true);
}

const scenarios = {};
let offset = 0;
for (const vus of LEVELS) {
  scenarios[`c${vus}`] = {
    executor: 'constant-vus', vus, duration: STAGE, startTime: `${offset}s`, exec: 'read', tags: { level: String(vus) },
  };
  offset += Number.parseInt(STAGE, 10) * (STAGE.endsWith('m') ? 60 : 1) + 5;
}

const thresholds = {
  't5042_server_ms{kind:access}': [`p(95)<${ACCESS_P95_MS}`],
  // A gateway that sends no Server-Timing leaves every server-time budget unmeasured: a FAIL.
  t5042_unmeasured: ['count==0'],
  checks: ['rate>0.99'],
};
if (EVALUATIONS) thresholds['t5042_server_ms{kind:evaluations}'] = [`p(95)<${EVALUATIONS_P95_MS}`];
// One sub-metric per kind and level, so the summary carries the report's table.
for (const kind of Object.keys(KINDS)) {
  for (const vus of LEVELS) {
    thresholds[`t5042_server_ms{kind:${kind},level:${vus}}`] = ['p(99)>=0'];
    thresholds[`t5042_broker_ms{kind:${kind},level:${vus}}`] = ['p(99)>=0'];
    thresholds[`t5042_round_trip_ms{kind:${kind},level:${vus}}`] = ['p(99)>=0'];
  }
}

export const options = {
  scenarios,
  thresholds,
  summaryTrendStats: ['p(50)', 'p(95)', 'p(99)', 'count'],
};

function ask(base, kind, tags) {
  const [method, path, body] = KINDS[kind];
  const params = { timeout: '30s', tags: { kind, ...tags }, headers: body ? JSON_HEADERS : {} };
  return http.request(method, `${base}${path}`, body || null, params);
}

export function setup() {
  if (!ENDPOINT_URL) fail('set ENDPOINT_URL to the root of an endpoint, e.g. https://api.host/api/endpoint/{slug}');
  const served = Object.keys(KINDS).filter((kind) => {
    const res = ask(ENDPOINT_URL, kind, { phase: 'probe' });
    if (res.status !== 200) console.warn(`${kind}: ${res.status} from ${ENDPOINT_URL}, left out`);
    return res.status === 200;
  });
  const residual = RESIDUAL_URL
    ? RESIDUAL_KINDS.filter((kind) => served.includes(kind) && ask(RESIDUAL_URL, kind, { phase: 'probe' }).status === 200)
    : [];
  if (!served.includes('access')) fail(`${ENDPOINT_URL}/access is not served: no Endpoint there`);
  return { served, residual };
}

export function read(state) {
  for (const kind of state.served) {
    const res = ask(ENDPOINT_URL, kind, {});
    check(res, { 'served': (r) => r.status === 200 });
    roundTripMs.add(res.timings.duration, { kind });
    const total = serverTiming(res.headers['Server-Timing'], 'total');
    if (total === undefined) {
      unmeasured.add(1, { kind });
    } else {
      serverMs.add(total, { kind });
      brokerMs.add(serverTiming(res.headers['Server-Timing'], 'broker') || 0, { kind });
    }
    if (state.residual.includes(kind)) {
      compared[`${kind}_open`].add(total === undefined ? res.timings.waiting : total);
      const narrowed = ask(RESIDUAL_URL, kind, { endpoint: 'residual' });
      check(narrowed, { 'served': (r) => r.status === 200 });
      const time = serverTiming(narrowed.headers['Server-Timing'], 'total');
      compared[`${kind}_residual`].add(time === undefined ? narrowed.timings.waiting : time);
    }
    if (THINK_MS > 0) sleep(THINK_MS / 1000);
  }
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const classes = {};
  for (const kind of RESIDUAL_KINDS) {
    if (data.metrics[`t5042_${kind}_residual_ms`]) {
      classes[`${kind} with idPattern, q, geoQ`] = [`t5042_${kind}_open_ms`, `t5042_${kind}_residual_ms`];
    }
  }
  const residual = RESIDUAL_URL ? fairShare(data.metrics, classes, RESIDUAL_TOLERANCE) : { ok: true, lines: ['residual constraints not measured: RESIDUAL_URL unset'] };
  const text = [
    // The p(99)>=0 thresholds only make k6 keep each table cell; they judge nothing.
    summarize(data, `k6 policy budgets (T-5042) against ${ENDPOINT_URL}, ${LEVELS.join('/')} clients, ${STAGE} each`)
      .trimEnd().split('\n').filter((line) => !line.endsWith('p(99)>=0')).join('\n'),
    ...residual.lines,
    '',
    timingTable(data.metrics, Object.keys(KINDS), LEVELS),
  ].join('\n');
  const report = { residual, metrics: data.metrics };
  return { stdout: `${text}\n`, [`${dir}/summary.txt`]: `${text}\n`, [`${dir}/summary.json`]: JSON.stringify(report, null, 2) };
}

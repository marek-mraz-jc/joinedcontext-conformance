// Fair share under an anonymous flood (T-5124). Three classes run side by side for the whole test:
// a pipeline writing batches with its token, a signed-in steward reading pages with theirs, and,
// after a baseline phase, an anonymous flood of reads at JC_K6_FLOOD_RATE (5x the normal anonymous
// rate by default). The verdict compares each signed-in class's p95 under the flood with its p95
// before it: within 20 % (JC_K6_TOLERANCE), and every anonymous answer is a 200 or a 429 with
// Retry-After. Run the flood from many source addresses (k6 --local-ips) so the per-address edge
// limit is not what sheds it: the gateway's anonymous share must.
//
// The tokens come from the environment only and are never printed. The writes upsert
// JC_K6_WRITES_PER_BATCH entities urn:ngsi-ld:{type}:k6-fair-share-{n} again and again, and teardown
// deletes them.
import http from 'k6/http';
import exec from 'k6/execution';
import { check, fail, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { fairShare, summarize } from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');
const seconds = (value) => {
  const match = /^(\d+)(s|m)$/.exec(value);
  if (!match) fail(`a duration is like 60s or 2m, not ${value}`);
  return Number(match[1]) * (match[2] === 'm' ? 60 : 1);
};

const ENDPOINT_URL = stripSlash(__ENV.ENDPOINT_URL);
const WRITE_URL = stripSlash(__ENV.WRITE_URL);
const WRITE_TOKEN = __ENV.WRITE_TOKEN || '';
const STEWARD_TOKEN = __ENV.STEWARD_TOKEN || '';
const WRITE_TYPE = __ENV.JC_K6_WRITE_TYPE || '';
const READ_PATH = __ENV.JC_K6_READ_PATH || '/ngsi-ld/v1/entities?limit=20';
const FLOOD_PATH = __ENV.JC_K6_FLOOD_PATH || READ_PATH;
const BASELINE = __ENV.JC_K6_BASELINE || '1m';
const FLOOD = __ENV.JC_K6_FLOOD || '2m';
const NORMAL_RATE = Number(__ENV.JC_K6_NORMAL_RATE || 20);
const FLOOD_RATE = Number(__ENV.JC_K6_FLOOD_RATE || NORMAL_RATE * 5);
const WRITE_RATE = Number(__ENV.JC_K6_WRITE_RATE || 2);
const STEWARD_RATE = Number(__ENV.JC_K6_STEWARD_RATE || 2);
const WRITES_PER_BATCH = Number(__ENV.JC_K6_WRITES_PER_BATCH || 10);
const TOLERANCE = Number(__ENV.JC_K6_TOLERANCE || 0.2);

const baselineMs = seconds(BASELINE) * 1000;
const total = `${seconds(BASELINE) + seconds(FLOOD)}s`;

const writeBase = new Trend('t5124_write_ms_base', true);
const writeFlood = new Trend('t5124_write_ms_flood', true);
const stewardBase = new Trend('t5124_steward_ms_base', true);
const stewardFlood = new Trend('t5124_steward_ms_flood', true);
const anonymousShed = new Counter('t5124_anonymous_429');
const anonymousServed = new Counter('t5124_anonymous_200');

export const CLASSES = {
  'pipeline write': ['t5124_write_ms_base', 't5124_write_ms_flood'],
  'signed-in steward read': ['t5124_steward_ms_base', 't5124_steward_ms_flood'],
};

export const options = {
  scenarios: {
    pipeline: {
      executor: 'constant-arrival-rate', rate: WRITE_RATE, timeUnit: '1s', duration: total,
      preAllocatedVUs: 4, maxVUs: 20, exec: 'pipeline',
    },
    steward: {
      executor: 'constant-arrival-rate', rate: STEWARD_RATE, timeUnit: '1s', duration: total,
      preAllocatedVUs: 4, maxVUs: 20, exec: 'steward',
    },
    flood: {
      executor: 'constant-arrival-rate', rate: FLOOD_RATE, timeUnit: '1s', duration: FLOOD,
      startTime: BASELINE, preAllocatedVUs: Math.min(FLOOD_RATE, 200), maxVUs: 1000, exec: 'flood',
    },
  },
  thresholds: {
    'checks{kind:write}': ['rate>0.99'],
    'checks{kind:steward}': ['rate>0.99'],
    'checks{kind:anonymous}': ['rate>0.99'],
  },
};

const ids = Array.from({ length: WRITES_PER_BATCH }, (_, n) => `urn:ngsi-ld:${WRITE_TYPE}:k6-fair-share-${n}`);
const json = (token) => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${token}` });
const flooding = () => Date.now() - exec.scenario.startTime >= baselineMs;

export function setup() {
  if (!ENDPOINT_URL) fail('set ENDPOINT_URL to the root of an Endpoint, e.g. https://api.host/api/endpoint/{slug}');
  if (!WRITE_URL) fail('set WRITE_URL to the NGSI-LD root a pipeline writes to, e.g. https://host/cs/{space}/ngsi-ld/v1');
  if (!WRITE_TYPE) fail('set JC_K6_WRITE_TYPE to an entity type the write token may upsert, e.g. FairShareProbe');
  if (!WRITE_TOKEN || !STEWARD_TOKEN) fail('set WRITE_TOKEN (a pipeline service account) and STEWARD_TOKEN (a signed-in person)');
  if (!(TOLERANCE > 0 && TOLERANCE < 1)) fail('JC_K6_TOLERANCE is a fraction, e.g. 0.2 for 20 %');
}

export function pipeline() {
  const batch = ids.map((id) => ({ id, type: WRITE_TYPE, name: { type: 'Property', value: `k6 fair share ${Date.now()}` } }));
  const res = http.post(`${WRITE_URL}/entityOperations/upsert`, JSON.stringify(batch), {
    headers: json(WRITE_TOKEN), timeout: '30s', tags: { kind: 'write' },
  });
  (flooding() ? writeFlood : writeBase).add(res.timings.duration);
  check(res, { 'the pipeline batch is written': (r) => r.status === 201 || r.status === 204 }, { kind: 'write' });
}

export function steward() {
  const res = http.get(`${ENDPOINT_URL}${READ_PATH}`, {
    headers: { Authorization: `Bearer ${STEWARD_TOKEN}` }, timeout: '30s', tags: { kind: 'steward' },
  });
  (flooding() ? stewardFlood : stewardBase).add(res.timings.duration);
  check(res, { 'the steward reads a page': (r) => r.status === 200 }, { kind: 'steward' });
}

export function flood() {
  const res = http.get(`${ENDPOINT_URL}${FLOOD_PATH}`, { timeout: '30s', tags: { kind: 'anonymous' } });
  if (res.status === 429) anonymousShed.add(1);
  if (res.status === 200) anonymousServed.add(1);
  check(res, {
    'an anonymous read is served or answered 429 with Retry-After': (r) => r.status === 200
      || (r.status === 429 && Number(r.headers['Retry-After']) > 0),
  }, { kind: 'anonymous' });
  if (res.status === 429) sleep(Math.min(Number(res.headers['Retry-After']) || 1, 5));
}

export function teardown() {
  http.post(`${WRITE_URL}/entityOperations/delete`, JSON.stringify(ids), {
    headers: json(WRITE_TOKEN), timeout: '30s', tags: { kind: 'cleanup' },
  });
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const verdict = fairShare(data.metrics, CLASSES, TOLERANCE);
  const count = (name) => (data.metrics[name] ? data.metrics[name].values.count : 0);
  const text = [
    summarize(data, `k6 fair share (T-5124): ${BASELINE} baseline, then ${FLOOD} of anonymous reads at ${FLOOD_RATE}/s against ${ENDPOINT_URL}`).trimEnd(),
    ...verdict.lines,
    `anonymous: ${count('t5124_anonymous_200')} served, ${count('t5124_anonymous_429')} answered 429`,
    `${verdict.ok ? 'PASS' : 'FAIL'} fair share`,
  ].join('\n');
  const report = { fairShare: verdict, metrics: data.metrics };
  return { stdout: `${text}\n`, [`${dir}/summary.txt`]: `${text}\n`, [`${dir}/summary.json`]: JSON.stringify(report, null, 2) };
}

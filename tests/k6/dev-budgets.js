// The nightly endpoint budget on dev (T-2800): 100 entities at 20 requests a second, p95 under
// 300 ms, through an endpoint and through the canonical space surface, one after the other so
// the node never carries both. scripts/budgets-dev.py reads the summary this writes and decides.
//
//   ENDPOINT_URL  NGSI-LD root of an endpoint, e.g. https://host/api/endpoint/<slug>/ngsi-ld/v1
//   SPACE_URL     NGSI-LD root of the same space's canonical surface, e.g. https://host/cs/<space>/ngsi-ld/v1
//   BUDGET_TYPE   the entity type both serve
//   BUDGET_TOKEN  a reading token (optional on a public endpoint)
//   JC_K6_RATE (20), JC_K6_DURATION (2m)
import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { durationSeconds } from './budgets.js';

export const ENDPOINT_MS = 'budget_endpoint_read_ms';
export const SPACE_MS = 'budget_space_read_ms';
const endpointMs = new Trend(ENDPOINT_MS, true);
const spaceMs = new Trend(SPACE_MS, true);
// A 429 means the surface's own rate limit sat below the offered load: the latency of a refusal
// is not the latency of a read, so the checker reports the run as not measured.
export const ENDPOINT_REFUSED = 'budget_endpoint_rate_limited';
export const SPACE_REFUSED = 'budget_space_rate_limited';
const endpointRefused = new Counter(ENDPOINT_REFUSED);
const spaceRefused = new Counter(SPACE_REFUSED);

const rate = Number(__ENV.JC_K6_RATE || 20);
const duration = __ENV.JC_K6_DURATION || '2m';
const type = __ENV.BUDGET_TYPE;
if (!__ENV.ENDPOINT_URL || !__ENV.SPACE_URL || !type) {
  throw new Error('set ENDPOINT_URL, SPACE_URL and BUDGET_TYPE');
}
const headers = { Accept: 'application/json', ...(__ENV.BUDGET_TOKEN ? { Authorization: `Bearer ${__ENV.BUDGET_TOKEN}` } : {}) };

function scenario(exec, startTime) {
  return {
    executor: 'constant-arrival-rate', exec, rate, timeUnit: '1s', duration, startTime,
    preAllocatedVUs: rate, maxVUs: rate * 5,
  };
}

export const options = {
  scenarios: {
    endpoint: scenario('endpoint', '0s'),
    space: scenario('space', `${durationSeconds(duration) + 10}s`),
  },
  thresholds: {
    [ENDPOINT_MS]: ['p(95)<300'],
    [SPACE_MS]: ['p(95)<300'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['p(50)', 'p(95)', 'max', 'count'],
};

function read(root, trend, refused) {
  const response = http.get(`${root}/entities?type=${encodeURIComponent(type)}&limit=100`, { headers });
  if (response.status === 429) {
    refused.add(1);
    return;
  }
  trend.add(response.timings.duration);
  check(response, {
    'answers 200': (r) => r.status === 200,
    'with entities': (r) => r.status === 200 && Array.isArray(r.json()) && r.json().length > 0,
  });
}

export function endpoint() {
  read(__ENV.ENDPOINT_URL, endpointMs, endpointRefused);
}

export function space() {
  read(__ENV.SPACE_URL, spaceMs, spaceRefused);
}

export function handleSummary(data) {
  const out = __ENV.JC_REPORTS_DIR || '.';
  return { [`${out}/dev-budgets.json`]: JSON.stringify(data, null, 2) };
}

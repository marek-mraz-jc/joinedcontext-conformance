// Slow neighbours (T-4903): five clients loop a read the broker takes seconds over, a sixth reads
// one entity. The gateway weighs a read past 2 s more against its caller's share and a slow budget
// of half its slots, and ends a plain read at 10 s with 503, a problem document and Retry-After, so
// the quick reader stays fast while the slow ones run.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Trend } from 'k6/metrics';
import { summarize } from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');

const ENDPOINT_URL = stripSlash(__ENV.ENDPOINT_URL);
const SLOW_PATH = __ENV.JC_K6_SLOW_PATH || '/ngsi-ld/v1/attributes';
const PROBE_PATH = __ENV.JC_K6_PROBE_PATH || '/ngsi-ld/v1/entities?limit=1';
const SLOW_LOOPS = Number(__ENV.JC_K6_SLOW_LOOPS || 5);
const DURATION = __ENV.JC_K6_DURATION || '2m';
const PROBE_P95_MS = Number(__ENV.JC_K6_PROBE_P95_MS || 300);
// The gateway's READ_DEADLINE plus the edge's and the network's share.
const DEADLINE_MS = Number(__ENV.JC_K6_DEADLINE_MS || 12000);

const probeMs = new Trend('t4903_probe_ms', true);
const slowMs = new Trend('t4903_slow_read_ms', true);

export const options = {
  scenarios: {
    slow: {
      executor: 'constant-vus', vus: SLOW_LOOPS, duration: DURATION, exec: 'slowLoop',
    },
    probe: {
      executor: 'constant-arrival-rate', rate: 2, timeUnit: '1s', duration: DURATION,
      preAllocatedVUs: 4, exec: 'probe',
    },
  },
  thresholds: {
    t4903_probe_ms: [`p(95)<${PROBE_P95_MS}`],
    t4903_slow_read_ms: [`max<${DEADLINE_MS}`],
    'checks{kind:probe}': ['rate>0.99'],
    'checks{kind:slow}': ['rate>0.99'],
  },
};

export function setup() {
  if (!ENDPOINT_URL) {
    fail('set ENDPOINT_URL to the root of an endpoint, e.g. https://api.host/api/endpoint/{slug}');
  }
}

export function slowLoop() {
  const res = http.get(`${ENDPOINT_URL}${SLOW_PATH}`, { timeout: '60s', tags: { kind: 'slow' } });
  slowMs.add(res.timings.duration);
  check(res, {
    'a slow read is answered or stopped with a problem and Retry-After': (r) => r.status === 200
      || (r.status === 503 && Number(r.headers['Retry-After']) > 0
        && (r.headers['Content-Type'] || '').includes('problem+json')),
  }, { kind: 'slow' });
  if (res.status === 503) sleep(Number(res.headers['Retry-After']) || 1);
}

export function probe() {
  const res = http.get(`${ENDPOINT_URL}${PROBE_PATH}`, { timeout: '10s', tags: { kind: 'probe' } });
  probeMs.add(res.timings.duration);
  check(res, { 'the quick read is served': (r) => r.status === 200 }, { kind: 'probe' });
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const text = summarize(
    data,
    `k6 slow neighbours (T-4903): ${SLOW_LOOPS} loops of ${SLOW_PATH} beside a probe of ${PROBE_PATH} for ${DURATION} against ${ENDPOINT_URL}`,
  );
  return { stdout: text, [`${dir}/summary.txt`]: text, [`${dir}/summary.json`]: JSON.stringify(data, null, 2) };
}

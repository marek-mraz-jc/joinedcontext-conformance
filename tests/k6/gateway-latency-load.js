// Gateway latency and throughput benchmark (T-0065, TS-22, EP-28, OPS-18).
//
// EP-28 budgets the gateway *overhead*, not the total read latency, so the run measures both
// hops: one scenario drives the space surface at the target rate, a second one reads the same
// entity through the gateway and straight from the broker's internal listener and records the
// paired difference. Without BROKER_URL that difference cannot be measured and the run reports
// throughput and ingress latency only.
import http from 'k6/http';
import { check, fail } from 'k6';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';
import { BROKER_MS, GATEWAY_MS, GATEWAY_READS, OVERHEAD_MS, budgets, summarize } from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');

const GATEWAY_URL = stripSlash(__ENV.GATEWAY_URL);
const BROKER_URL = stripSlash(__ENV.BROKER_URL);
const TOKEN = __ENV.GATEWAY_TOKEN || '';
const BROKER_TENANT = __ENV.BROKER_TENANT || '';
const ENTITY_TYPE = __ENV.ENTITY_TYPE || '';

// the load shape is tunable, the budgets are not: no environment variable may lower a threshold
const RATE = Number(__ENV.JC_K6_RATE || 5000); // TS-22: 5 000 req/s per replica
const DURATION = __ENV.JC_K6_DURATION || '1m';
const VUS = Number(__ENV.JC_K6_VUS || 50); // the task's 50 concurrent readers
const OVERHEAD_RATE = Number(__ENV.JC_K6_OVERHEAD_RATE || 20);
const WARMUP_READS = Number(__ENV.JC_K6_WARMUP_READS || 20); // TS-22 budgets *cached* policy evaluations

const overheadMs = new Trend(OVERHEAD_MS);
const gatewayMs = new Trend(GATEWAY_MS);
const brokerMs = new Trend(BROKER_MS);
const gatewayReads = new Counter(GATEWAY_READS);

const scenarios = {
  ts22_throughput: {
    executor: 'constant-arrival-rate',
    rate: RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: VUS,
    maxVUs: VUS * 20,
    exec: 'gatewayRead',
    gracefulStop: '10s',
  },
};
if (BROKER_URL) {
  // EP-28 budgets the overhead *under* the sustained load, so this runs beside the throughput scenario
  scenarios.ep28_overhead = {
    executor: 'constant-arrival-rate',
    rate: OVERHEAD_RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: 10,
    maxVUs: 50,
    exec: 'pairedRead',
    gracefulStop: '10s',
  };
}

export const options = {
  scenarios,
  thresholds: budgets({ rate: RATE, duration: DURATION, measureOverhead: Boolean(BROKER_URL) }),
};

function params(tag) {
  const headers = { Accept: 'application/ld+json' };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  return { headers, tags: { hop: tag } };
}

function brokerParams() {
  const p = params('broker');
  if (BROKER_TENANT) p.headers['NGSILD-Tenant'] = BROKER_TENANT;
  return p;
}

export function setup() {
  if (!GATEWAY_URL) fail('set GATEWAY_URL to the NGSI-LD API root of a space, e.g. https://host/cs/ovzdusie/ngsi-ld/v1');
  let id = __ENV.ENTITY_ID;
  if (!id) {
    const query = ENTITY_TYPE ? `?type=${encodeURIComponent(ENTITY_TYPE)}&limit=1` : '?limit=1';
    const res = http.get(`${GATEWAY_URL}/entities${query}`, params('discovery'));
    if (res.status !== 200) fail(`entity discovery answered ${res.status}; set ENTITY_ID to skip it`);
    const body = res.json();
    if (!Array.isArray(body) || body.length === 0 || !body[0].id) {
      fail('no entity to read: the space is empty or narrowed to nothing; set ENTITY_ID');
    }
    id = body[0].id;
  }
  // TS-22 budgets cached policy evaluations, so the cache is warm before the first measurement
  for (let i = 0; i < WARMUP_READS; i++) {
    const res = http.get(`${GATEWAY_URL}/entities/${encodeURIComponent(id)}`, params('warmup'));
    if (res.status !== 200) fail(`warm-up read of ${id} answered ${res.status}`);
  }
  return { entityId: id };
}

function read(base, id, requestParams) {
  return http.get(`${base}/entities/${encodeURIComponent(id)}`, requestParams);
}

export function gatewayRead(data) {
  const res = read(GATEWAY_URL, data.entityId, params('gateway'));
  gatewayMs.add(res.timings.duration);
  gatewayReads.add(1);
  check(res, {
    'TS-22 gateway read is 200': (r) => r.status === 200,
    'TS-22 gateway returned the entity that was asked for': (r) => {
      const body = r.json();
      return body !== null && body.id === data.entityId;
    },
  });
}

export function pairedRead(data) {
  // alternate the order so a warm connection does not always favour the same hop
  const brokerFirst = exec.scenario.iterationInTest % 2 === 0;
  const first = brokerFirst
    ? read(BROKER_URL, data.entityId, brokerParams())
    : read(GATEWAY_URL, data.entityId, params('gateway'));
  const second = brokerFirst
    ? read(GATEWAY_URL, data.entityId, params('gateway'))
    : read(BROKER_URL, data.entityId, brokerParams());
  const broker = brokerFirst ? first : second;
  const gateway = brokerFirst ? second : first;

  const paired = check(
    { broker, gateway },
    {
      'EP-28 broker read is 200': (r) => r.broker.status === 200,
      'EP-28 gateway read is 200': (r) => r.gateway.status === 200,
    },
  );
  if (!paired) return; // a failed hop has no meaningful duration; do not poison the overhead trend
  brokerMs.add(broker.timings.duration);
  gatewayMs.add(gateway.timings.duration);
  overheadMs.add(gateway.timings.duration - broker.timings.duration);
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const measured = BROKER_URL ? '' : ' (BROKER_URL unset: the EP-28 overhead was NOT measured)';
  const text = summarize(data, `k6 gateway benchmark (T-0065): ${RATE} req/s for ${DURATION} against ${GATEWAY_URL}${measured}`);
  return { stdout: text, [`${dir}/summary.txt`]: text, [`${dir}/summary.json`]: JSON.stringify(data, null, 2) };
}

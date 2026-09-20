// Sustained load on one Endpoint's representations (T-0332, EP-05, EP-20, EP-28, TS-05).
//
// The task asks for 100 rps against `/ngsi-ld/v1/entities` and `/file.geojson` with a p99
// under 5.0 ms. The 5 ms is EP-28's budget on the gateway's own *overhead*, so the run pairs
// each measured read against the broker's internal listener and asserts the difference; a
// p99 on the total read would be a budget on the broker, the network and the size of the
// dataset instead of on anything the gateway decides. The total read is still asserted, at
// the OPS-18 ingress budget, and the download at its first byte.
//
// Rate limiting is the other half (EP-20). The sustained phase runs under the endpoint's
// configured limit and must never be shed; with JC_K6_BURST_RATE a second phase deliberately
// exceeds it and must be, which is the only way to tell a limiter from a missing one.
import http from 'k6/http';
import { check, fail } from 'k6';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';
import {
  ENDPOINT_MS,
  ENDPOINT_OVERHEAD_MS,
  ENDPOINT_READS,
  BROKER_MS,
  GEOJSON_TTFB_MS,
  LIMIT_HEADERS_MISSING,
  LIMIT_SHED,
  endpointBudgets,
  summarize,
} from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');

const ENDPOINT_URL = stripSlash(__ENV.ENDPOINT_URL);
const NGSI_URL = `${ENDPOINT_URL}/ngsi-ld/v1`;
const GEOJSON_URL = `${ENDPOINT_URL}/file.geojson`;
const BROKER_URL = stripSlash(__ENV.BROKER_URL);
const BROKER_TENANT = __ENV.BROKER_TENANT || '';
const TOKEN = __ENV.GATEWAY_TOKEN || '';

// the load shape is tunable, the budgets are not: no environment variable may lower a threshold
const RATE = Number(__ENV.JC_K6_RATE || 100); // the task's sustained 100 rps
const DURATION = __ENV.JC_K6_DURATION || '1m';
const VUS = Number(__ENV.JC_K6_VUS || 20);
const GEOJSON_RATE = Number(__ENV.JC_K6_GEOJSON_RATE || 5);
const OVERHEAD_RATE = Number(__ENV.JC_K6_OVERHEAD_RATE || 10);
const BURST_RATE = Number(__ENV.JC_K6_BURST_RATE || 0);
const BURST_DURATION = __ENV.JC_K6_BURST_DURATION || '10s';
const WARMUP_READS = Number(__ENV.JC_K6_WARMUP_READS || 20); // EP-28 budgets *cached* evaluations

const endpointMs = new Trend(ENDPOINT_MS);
const brokerMs = new Trend(BROKER_MS);
const overheadMs = new Trend(ENDPOINT_OVERHEAD_MS);
const geojsonTtfbMs = new Trend(GEOJSON_TTFB_MS);
const endpointReads = new Counter(ENDPOINT_READS);
const limitHeadersMissing = new Counter(LIMIT_HEADERS_MISSING);
const limitShed = new Counter(LIMIT_SHED);

const scenarios = {
  ts05_entities: {
    executor: 'constant-arrival-rate',
    rate: RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: VUS,
    maxVUs: VUS * 20,
    exec: 'entitiesRead',
    gracefulStop: '10s',
  },
  ep05_geojson: {
    executor: 'constant-arrival-rate',
    rate: GEOJSON_RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: 5,
    maxVUs: 50,
    exec: 'geojsonRead',
    gracefulStop: '30s',
  },
};
if (BROKER_URL) {
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
if (BURST_RATE > 0) {
  // after the sustained phase, so its 429s cannot starve the reads that must all answer 200
  scenarios.ep20_burst = {
    executor: 'constant-arrival-rate',
    rate: BURST_RATE,
    timeUnit: '1s',
    duration: BURST_DURATION,
    startTime: DURATION,
    preAllocatedVUs: VUS,
    maxVUs: VUS * 20,
    exec: 'burstRead',
    gracefulStop: '5s',
  };
}

export const options = {
  scenarios,
  thresholds: endpointBudgets({
    rate: RATE,
    duration: DURATION,
    measureOverhead: Boolean(BROKER_URL),
    proveRateLimit: BURST_RATE > 0,
  }),
};

function params(phase, hop) {
  const headers = { Accept: 'application/ld+json' };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  return { headers, tags: { phase, hop } };
}

function brokerParams() {
  const p = params('sustained', 'broker');
  if (BROKER_TENANT) p.headers['NGSILD-Tenant'] = BROKER_TENANT;
  return p;
}

/**
 * Counts an answer that arrived without the standard RateLimit field.
 *
 * An endpoint that declares no limit sends none, which is not a defect, so the count only
 * matters where the run is proving the limiter (EP-20, MIM0-R7).
 */
function countLimitHeaders(res) {
  if (BURST_RATE <= 0) return;
  const advertised = res.headers['Ratelimit-Limit'] || res.headers['RateLimit-Limit'];
  if (!advertised) limitHeadersMissing.add(1);
}

export function setup() {
  if (!ENDPOINT_URL) {
    fail('set ENDPOINT_URL to the root of an endpoint, e.g. https://host/api/endpoint/<slug>');
  }
  // A query that selects nothing is a 400 on a conformant broker, and the endpoint's own
  // policy may name no type at all, so the type comes from the surface rather than a guess.
  let type = __ENV.ENTITY_TYPE;
  if (!type) {
    const res = http.get(`${NGSI_URL}/types`, params('setup', 'discovery'));
    if (res.status !== 200) fail(`type discovery answered ${res.status}; set ENTITY_TYPE to skip it`);
    const body = res.json();
    const list = (body && body.typeList) || [];
    if (list.length === 0) fail('the endpoint publishes no entity type; set ENTITY_TYPE');
    type = typeof list[0] === 'string' ? list[0] : list[0].id || list[0].typeName;
  }

  let id = __ENV.ENTITY_ID;
  if (!id) {
    const query = `?type=${encodeURIComponent(type)}&limit=1`;
    const res = http.get(`${NGSI_URL}/entities${query}`, params('setup', 'discovery'));
    if (res.status !== 200) fail(`entity discovery answered ${res.status}; set ENTITY_ID to skip it`);
    const body = res.json();
    if (!Array.isArray(body) || body.length === 0 || !body[0].id) {
      fail('no entity to read: the endpoint is narrowed to nothing; set ENTITY_ID');
    }
    id = body[0].id;
  }

  // EP-28 budgets a cached policy evaluation, so the cache is warm before the first measurement
  for (let i = 0; i < WARMUP_READS; i++) {
    const res = http.get(`${NGSI_URL}/entities/${encodeURIComponent(id)}`, params('setup', 'warmup'));
    if (res.status !== 200) fail(`warm-up read of ${id} answered ${res.status}`);
  }
  return { entityId: id, entityType: type };
}

function read(base, id, requestParams) {
  return http.get(`${base}/entities/${encodeURIComponent(id)}`, requestParams);
}

export function entitiesRead(data) {
  const res = read(NGSI_URL, data.entityId, params('sustained', 'endpoint'));
  endpointMs.add(res.timings.duration);
  endpointReads.add(1);
  countLimitHeaders(res);
  if (res.status === 429) limitShed.add(1);
  check(res, {
    'TS-05 endpoint read is 200': (r) => r.status === 200,
    'TS-05 endpoint returned the entity that was asked for': (r) => {
      const body = r.json();
      return body !== null && body.id === data.entityId;
    },
  });
}

export function geojsonRead() {
  const res = http.get(GEOJSON_URL, params('sustained', 'geojson'));
  // the first byte, not the whole download: the rest of it is the size of the dataset
  geojsonTtfbMs.add(res.timings.waiting);
  countLimitHeaders(res);
  if (res.status === 429) limitShed.add(1);
  check(res, {
    'EP-05 geojson download is 200': (r) => r.status === 200,
    'EP-05 geojson download is a FeatureCollection': (r) => {
      const body = r.json();
      return body !== null && body.type === 'FeatureCollection';
    },
  });
}

export function pairedRead(data) {
  // alternate the order so a warm connection does not always favour the same hop
  const brokerFirst = exec.scenario.iterationInTest % 2 === 0;
  const first = brokerFirst
    ? read(BROKER_URL, data.entityId, brokerParams())
    : read(NGSI_URL, data.entityId, params('sustained', 'endpoint'));
  const second = brokerFirst
    ? read(NGSI_URL, data.entityId, params('sustained', 'endpoint'))
    : read(BROKER_URL, data.entityId, brokerParams());
  const broker = brokerFirst ? first : second;
  const endpoint = brokerFirst ? second : first;

  const paired = check(
    { broker, endpoint },
    {
      'EP-28 broker read is 200': (r) => r.broker.status === 200,
      'EP-28 endpoint read is 200': (r) => r.endpoint.status === 200,
    },
  );
  if (!paired) return; // a failed hop has no meaningful duration; do not poison the overhead trend
  brokerMs.add(broker.timings.duration);
  endpointMs.add(endpoint.timings.duration);
  overheadMs.add(endpoint.timings.duration - broker.timings.duration);
}

export function burstRead(data) {
  const res = read(NGSI_URL, data.entityId, params('burst', 'endpoint'));
  countLimitHeaders(res);
  if (res.status === 429) limitShed.add(1);
  check(res, {
    // EP-20: over the limit the endpoint sheds; it never answers something else, and it never
    // serves past the limit without saying what the limit is.
    'EP-20 a burst is served or shed, never anything else': (r) => r.status === 200 || r.status === 429,
    'EP-20 a shed answer names the retry delay': (r) =>
      r.status !== 429 || Boolean(r.headers['Retry-After'] || r.headers['Ratelimit-Reset']),
  });
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const measured = BROKER_URL ? '' : ' (BROKER_URL unset: the EP-28 overhead was NOT measured)';
  const limiter = BURST_RATE > 0 ? ` + ${BURST_RATE} req/s burst for ${BURST_DURATION}` : ' (JC_K6_BURST_RATE unset: the EP-20 limiter was NOT proven)';
  const text = summarize(
    data,
    `k6 endpoint benchmark (T-0332): ${RATE} req/s for ${DURATION} against ${ENDPOINT_URL}${limiter}${measured}`,
  );
  return { stdout: text, [`${dir}/summary.txt`]: text, [`${dir}/summary.json`]: JSON.stringify(data, null, 2) };
}

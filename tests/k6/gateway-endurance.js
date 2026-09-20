// Gateway endurance and memory soak benchmark (T-0066, TS-22, OPS-18).
//
// TS-22 mandates zero memory leaks during a sustained 30-minute stress test.
// One scenario sustains continuous read load against the space surface while a probe
// scrapes process resident memory from Prometheus text metrics, tracking RSS slope
// across the evaluation window after initial warmup.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import {
  GATEWAY_MS,
  GATEWAY_READS,
  MEMORY_GROWTH,
  RSS_MB,
  RSS_SLOPE_PCT,
  durationSeconds,
  enduranceBudgets,
  slopePercent,
  summarize,
} from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');

const GATEWAY_URL = stripSlash(__ENV.GATEWAY_URL);
const GATEWAY_METRICS_URL = __ENV.GATEWAY_METRICS_URL || '';
const BROKER_METRICS_URL = __ENV.BROKER_METRICS_URL || '';
const TOKEN = __ENV.GATEWAY_TOKEN || '';
const ENTITY_TYPE = __ENV.ENTITY_TYPE || '';

// Load and probe tunables — budgets remain strictly defined in budgets.js
const RATE = Number(__ENV.JC_K6_RATE || 500);
const DURATION = __ENV.JC_K6_DURATION || '30m';
const VUS = Number(__ENV.JC_K6_VUS || 50);
const WARMUP_READS = Number(__ENV.JC_K6_WARMUP_READS || 20);

const PROBE_INTERVAL = __ENV.JC_K6_PROBE_INTERVAL || '30s';
const WARMUP = __ENV.JC_K6_WARMUP || '5m';
const MEMORY_WINDOW = __ENV.JC_K6_MEMORY_WINDOW || '20m';
const MEMORY_SLOPE = Number(__ENV.JC_K6_MEMORY_SLOPE || 0.05);

const gatewayMs = new Trend(GATEWAY_MS);
const gatewayReads = new Counter(GATEWAY_READS);
const rssMb = new Trend(RSS_MB);
const memoryGrowth = new Counter(MEMORY_GROWTH);
const rssSlopePct = new Trend(RSS_SLOPE_PCT);

const probeIntervalSec = durationSeconds(PROBE_INTERVAL);
const warmupSec = durationSeconds(WARMUP);
const windowSec = durationSeconds(MEMORY_WINDOW);
const durationSec = durationSeconds(DURATION);

const hasMetrics = Boolean(GATEWAY_METRICS_URL);

const scenarios = {
  ts22_soak: {
    executor: 'constant-arrival-rate',
    rate: RATE,
    timeUnit: '1s',
    duration: DURATION,
    preAllocatedVUs: VUS,
    maxVUs: VUS * 10,
    exec: 'gatewayRead',
    gracefulStop: '10s',
  },
};

if (hasMetrics) {
  scenarios.ts22_rss_probe = {
    executor: 'per-vu-iterations',
    vus: 1,
    iterations: 1,
    maxDuration: `${durationSec + 30}s`,
    exec: 'rssProbe',
    gracefulStop: '10s',
  };
}

export const options = {
  scenarios,
  thresholds: enduranceBudgets({ rate: RATE, duration: DURATION }),
};

function params(tag) {
  const headers = { Accept: 'application/ld+json' };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  return { headers, tags: { hop: tag } };
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

function parsePrometheusMetric(body, metricName) {
  const lines = body.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith('#')) continue;
    if (line.startsWith(metricName + ' ') || line.startsWith(metricName + '{')) {
      const parts = line.split(/\s+/);
      const val = parseFloat(parts[parts.length - 1]);
      if (!isNaN(val)) return val;
    }
  }
  return null;
}

function scrapeRss(metricsUrl, label) {
  const res = http.get(metricsUrl, { timeout: '10s', tags: { scrape: label } });
  const ok = check(res, {
    [`TS-22 ${label} metrics scrape is 200`]: (r) => r.status === 200,
  });
  if (!ok) return null;

  const body = res.body || '';
  let bytes = parsePrometheusMetric(body, 'process_resident_memory_bytes');
  if (bytes === null) {
    bytes = parsePrometheusMetric(body, 'container_memory_working_set_bytes');
  }
  const found = check(bytes, {
    [`TS-22 ${label} metrics exposed resident memory`]: (v) => v !== null,
  });
  if (!found || bytes === null) return null;
  return bytes / (1024 * 1024);
}

// Module-level series for probe VUs
const gatewaySeries = [];
const brokerSeries = [];

export function rssProbe() {
  // the counter must exist even on a leak-free run, otherwise k6 skips its threshold entirely
  memoryGrowth.add(0);
  const start = Date.now();
  let elapsedSec = 0;

  while (elapsedSec < durationSec) {
    const nowSec = (Date.now() - start) / 1000;
    if (GATEWAY_METRICS_URL) {
      const mb = scrapeRss(GATEWAY_METRICS_URL, 'gateway');
      if (mb !== null) {
        rssMb.add(mb);
        gatewaySeries.push({ t: nowSec, rss: mb });
      }
    }
    if (BROKER_METRICS_URL) {
      const mb = scrapeRss(BROKER_METRICS_URL, 'broker');
      if (mb !== null) {
        brokerSeries.push({ t: nowSec, rss: mb });
      }
    }
    sleep(probeIntervalSec);
    elapsedSec = (Date.now() - start) / 1000;
  }

  // End of run: evaluate slope over the window
  function evaluateProcess(series, name) {
    if (series.length < 2) return;
    const windowStart = Math.max(warmupSec, durationSec - windowSec);
    const windowSamples = series.filter((s) => s.t >= windowStart);
    const slope = slopePercent(windowSamples);
    rssSlopePct.add(slope, { process: name });
    if (slope > MEMORY_SLOPE) {
      memoryGrowth.add(1);
    }
    check(slope, {
      [`TS-22 ${name} memory growth within budget`]: (s) => s <= MEMORY_SLOPE,
    });
  }

  if (GATEWAY_METRICS_URL) evaluateProcess(gatewaySeries, 'gateway');
  if (BROKER_METRICS_URL) evaluateProcess(brokerSeries, 'broker');
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const measured = GATEWAY_METRICS_URL
    ? ''
    : ' (GATEWAY_METRICS_URL unset: the TS-22 memory budget was NOT measured)';
  let text = summarize(
    data,
    `k6 gateway endurance benchmark (T-0066): ${RATE} req/s for ${DURATION} against ${GATEWAY_URL}${measured}`,
  );

  // handleSummary runs in the init context: the probe VU's samples are gone, the metrics are not
  const rss = data.metrics[RSS_MB];
  const slope = data.metrics[RSS_SLOPE_PCT];
  if (GATEWAY_METRICS_URL) {
    // a k6 trend summary carries avg/min/med/max and percentiles, never a sample count
    text += rss
      ? `RSS: min ${rss.values.min.toFixed(2)} MB, max ${rss.values.max.toFixed(2)} MB, avg ${rss.values.avg.toFixed(2)} MB\n`
      : 'RSS: no sample was scraped at all — the probe never read the metrics endpoint\n';
    text += slope
      ? `slope over the final ${MEMORY_WINDOW}: max ${slope.values.max.toFixed(4)} %/min, budget ${MEMORY_SLOPE} %/min\n`
      : 'slope: not evaluated, the run held fewer than two samples in the window\n';
  }

  return {
    stdout: text,
    [`${dir}/summary.txt`]: text,
    [`${dir}/summary.json`]: JSON.stringify(data, null, 2),
  };
}

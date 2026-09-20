// Feeds the real budgets of budgets.js a synthetic distribution (T-0065).
//   JC_K6_CASE=good  a gateway inside every budget          -> the run must pass
//   JC_K6_CASE=bad   one that misses every one of them      -> the run must fail, naming each
// Timing on a shared machine is far too noisy to prove a 5 ms budget with real requests, so the
// budgets are proven here on samples and the request path is proven separately in selftest.py.
import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import {
  ENDPOINT_MS,
  ENDPOINT_OVERHEAD_MS,
  ENDPOINT_READS,
  GEOJSON_TTFB_MS,
  LIMIT_HEADERS_MISSING,
  LIMIT_SHED,
  endpointBudgets,
  EXPORT_MS,
  EXPORT_ROWS,
  EXPORT_TIMEOUTS,
  EXPORT_TTFB_MS,
  GATEWAY_MS,
  GATEWAY_READS,
  MEMORY_GROWTH,
  OVERHEAD_MS,
  RSS_SLOPE_PCT,
  budgets,
  enduranceBudgets,
  exportBudgets,
  slopePercent,
  summarize,
} from './budgets.js';

const CASE = __ENV.JC_K6_CASE || 'good';
const RATE = 100;
const DURATION = '10s';
const READS = RATE * 10;

const overheadMs = new Trend(OVERHEAD_MS);
const gatewayMs = new Trend(GATEWAY_MS);
const gatewayReads = new Counter(GATEWAY_READS);
const memoryGrowth = new Counter(MEMORY_GROWTH);
const rssSlopePct = new Trend(RSS_SLOPE_PCT);
const exportTtfbMs = new Trend(EXPORT_TTFB_MS);
const exportMs = new Trend(EXPORT_MS);
const exportRows = new Counter(EXPORT_ROWS);
const exportTimeouts = new Counter(EXPORT_TIMEOUTS);
const endpointOverheadMs = new Trend(ENDPOINT_OVERHEAD_MS);
const endpointMs = new Trend(ENDPOINT_MS);
const endpointReads = new Counter(ENDPOINT_READS);
const geojsonTtfbMs = new Trend(GEOJSON_TTFB_MS);
const limitHeadersMissing = new Counter(LIMIT_HEADERS_MISSING);
const limitShed = new Counter(LIMIT_SHED);

let thresholds = {};
if (CASE === 'good' || CASE === 'bad') {
  thresholds = budgets({ rate: RATE, duration: DURATION, measureOverhead: true });
} else if (CASE === 'endurance-good' || CASE === 'endurance-bad') {
  thresholds = enduranceBudgets({ rate: RATE, duration: DURATION });
} else if (CASE === 'export-good' || CASE === 'export-bad') {
  thresholds = exportBudgets({ rows: 1000, streams: 2 });
} else if (CASE === 'endpoint-good' || CASE === 'endpoint-bad') {
  // proveRateLimit, so the shed counter is asserted in the direction a real burst run uses;
  // the tagged http_req_failed submetric is proven by the wiring run, not by samples
  thresholds = endpointBudgets({
    rate: RATE,
    duration: DURATION,
    measureOverhead: true,
    proveRateLimit: true,
  });
  delete thresholds['http_req_failed{phase:sustained}'];
}

export const options = {
  scenarios: {
    budgets: { executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: '1m', exec: 'samples' },
  },
  thresholds,
};

export function samples() {
  if (CASE === 'good' || CASE === 'bad') {
    const good = CASE === 'good';
    // EP-28 (5 ms) and OPS-18 (50 ms p95): well inside, or a gateway that costs 30 ms over the broker
    for (let i = 0; i < READS; i++) {
      overheadMs.add(good ? 0.4 + (i % 10) / 10 : 30 + (i % 10));
      gatewayMs.add(good ? 8 + (i % 20) : 120 + (i % 20));
    }
    // TS-22 throughput: the full arrival rate, or a system that served two thirds of it
    gatewayReads.add(good ? READS : Math.floor(READS * 0.66));
    check(null, { 'TS-22 every read answered with the entity that was asked for': () => good });
    if (!good) {
      // http_req_failed: a request that cannot connect at all
      http.get('http://127.0.0.1:1/entities', { timeout: '1s' });
    }
  } else if (CASE === 'endurance-good' || CASE === 'endurance-bad') {
    const good = CASE === 'endurance-good';
    for (let i = 0; i < READS; i++) {
      gatewayMs.add(8 + (i % 20));
    }
    gatewayReads.add(READS);

    const series = [];
    for (let i = 0; i < 10; i++) {
      series.push({
        t: i,
        rss: good ? 100 + (i % 2 === 0 ? 0.1 : -0.1) : 100 + i * 5,
      });
    }
    const slope = slopePercent(series);
    rssSlopePct.add(slope);
    if (slope > 0.05) {
      memoryGrowth.add(1);
    } else {
      memoryGrowth.add(0);
    }

    check(null, { 'TS-22 endurance read answered 200': () => good });
    if (!good) {
      http.get('http://127.0.0.1:1/entities', { timeout: '1s' });
    }
  } else if (CASE === 'export-good' || CASE === 'export-bad') {
    const good = CASE === 'export-good';
    if (good) {
      exportTtfbMs.add(80);
      exportMs.add(1500);
      exportRows.add(2000);
      exportTimeouts.add(0);
    } else {
      exportTtfbMs.add(3500);
      exportMs.add(350000);
      exportRows.add(1000);
      exportTimeouts.add(1);
    }
    check(null, { 'EP-41 export answered 200': () => true });
  } else if (CASE === 'endpoint-good' || CASE === 'endpoint-bad') {
    const good = CASE === 'endpoint-good';
    // EP-28 (5 ms overhead) and OPS-18 (50 ms p95 total), or a gateway costing 30 ms over the broker
    for (let i = 0; i < READS; i++) {
      endpointOverheadMs.add(good ? 0.4 + (i % 10) / 10 : 30 + (i % 10));
      endpointMs.add(good ? 8 + (i % 20) : 120 + (i % 20));
    }
    // EP-05: the download answers its first byte quickly, or takes three and a half seconds
    for (let i = 0; i < 100; i++) {
      geojsonTtfbMs.add(good ? 60 + (i % 40) : 3500 + (i % 40));
    }
    endpointReads.add(good ? READS : Math.floor(READS * 0.66));
    // EP-20: a burst that was shed and answers that named the limit, or a limiter nobody reached
    limitShed.add(good ? 12 : 0);
    limitHeadersMissing.add(good ? 0 : 7);
    check(null, { 'TS-05 every endpoint read answered with the entity that was asked for': () => good });
  }
}

export function handleSummary(data) {
  return { stdout: summarize(data, `k6 budget self-test (T-0065), case ${CASE}`) };
}

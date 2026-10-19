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
  CSV_TTFB_MS,
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
import { parseRequests } from './requests.js';

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
const csvTtfbMs = new Trend(CSV_TTFB_MS);
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
} else if (CASE === 'requests') {
  thresholds = { checks: ['rate==1.0'] };
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
      csvTtfbMs.add(good ? 60 + (i % 40) : 3500 + (i % 40));
    }
    endpointReads.add(good ? READS : Math.floor(READS * 0.66));
    // EP-20: a burst that was shed and answers that named the limit, or a limiter nobody reached
    limitShed.add(good ? 12 : 0);
    limitHeadersMissing.add(good ? 0 : 7);
    check(null, { 'TS-05 every endpoint read answered with the entity that was asked for': () => good });
  } else if (CASE === 'requests') {
    // T-4714: the request list of api-mix-100rps.js
    const list = [
      '# comment',
      '',
      'ngsi\tGET\thttps://h/e/ngsi-ld/v1/entities?type=T',
      'schema\tGET\thttps://h/e/schema/index.json\t404',
      'mcp\tPOST\thttps://h/e/mcp\t200',
      'ws\tGET\twss://h/e/ws\r',
      '   ',
    ].join('\n');
    const { requests, skipped } = parseRequests(list);
    check(requests, {
      'an empty line and a comment are skipped, three rows are kept': (r) => r.length === 3,
      'the kept rows keep their kind, method and url': (r) =>
        r[0].kind === 'ngsi' && r[1].method === 'POST' && r[2].url === 'wss://h/e/ws',
    });
    check(skipped, { 'a 404 row is skipped with its status': (s) => s.length === 1 && s[0].status === '404' });
    const refuses = (text) => {
      try {
        parseRequests(text);
        return false;
      } catch (_) {
        return true;
      }
    };
    check(null, {
      'an unknown kind is refused': () => refuses('graphql\tGET\thttps://h/e'),
      'an unknown method is refused': () => refuses('ngsi\tPUT\thttps://h/e'),
      'a row without a url is refused': () => refuses('ngsi\tGET'),
      'a body on a GET is refused': () => refuses('ngsi\tGET\thttps://h/e\t200\t{}'),
      'a body that is not JSON is refused': () => refuses('mcp\tPOST\thttps://h/e/mcp\t200\t{call'),
    });
    // T-4754: geo and temporal are kinds of their own, and an MCP row may carry its tools/call.
    const call = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"query_entities"}}';
    const typed = parseRequests(
      `geo\tGET\thttps://h/e/ngsi-ld/v1/entities?type=T&georel=near\ntemporal\tGET\thttps://h/e/ngsi-ld/v1/temporal/entities?type=T&lastN=5\nmcp\tPOST\thttps://h/e/mcp\t200\t${call}`,
    ).requests;
    check(typed, {
      'geo and temporal rows are kept': (r) => r[0].kind === 'geo' && r[1].kind === 'temporal',
      'an MCP row keeps its body': (r) => r[2].body === call && r[0].body === undefined,
    });
  }
}

export function handleSummary(data) {
  return { stdout: summarize(data, `k6 budget self-test (T-0065), case ${CASE}`) };
}

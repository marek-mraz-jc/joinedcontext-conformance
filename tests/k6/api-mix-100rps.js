// A mix of distinct API requests at a constant arrival rate across the Endpoint interfaces
// (T-4714): NGSI-LD, OData, OGC, MCP, schema, CSV and CKAN over HTTP, plus WebSocket subscribers
// held open for the whole run. Anonymous only: the script sends no token.
//
//   JC_K6_REQUESTS   the list (requests.js): kind, method, url[, probed status], one per line
//   JC_K6_RATE       requests per second (default 100)
//   JC_K6_DURATION   how long (default 1m)
//   JC_K6_VUS        pre-allocated VUs (default 60: 100/s at 0.3 s each needs 30)
//   JC_K6_WS_SUBS    WebSocket subscribers spread over the list's ws rows (default 10, the
//                    gateway's per-caller cap EP-107)
//   JC_K6_RAMP       <from>:<to>:<time>, e.g. 50:500:5m (T-4754): the rate climbs from <from> to
//                    <to> over <time>, then holds <to> for JC_K6_DURATION; JC_K6_RATE is unused
//   JC_K6_P95_MS     each kind's p95 budget in ms (default 1000; T-4754 asks 500)
// Whatever the budget, a run whose p95 passes 2 s or whose errors pass 2 % aborts once it has run
// a minute, so a production run never keeps a slow or failing stack under load (T-4754).
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { Rate, Counter } from 'k6/metrics';
import { WebSocket } from 'k6/websockets';
import { setTimeout } from 'k6/timers';
import { parseRequests } from './requests.js';

const DURATION = __ENV.JC_K6_DURATION || '1m';
const P95 = Number(__ENV.JC_K6_P95_MS || 1000);

function secondsOf(text, name) {
  const m = /^(\d+)(s|m)$/.exec(text);
  if (!m) {
    throw new Error(`${name} '${text}': use <n>s or <n>m`);
  }
  return Number(m[1]) * (m[2] === 'm' ? 60 : 1);
}

// The ramp, when one is asked for: from, to and its length in seconds.
export function rampOf(text) {
  if (!text) {
    return null;
  }
  const parts = text.split(':');
  const [from, to] = parts.slice(0, 2).map(Number);
  if (parts.length !== 3 || !(from > 0) || !(to >= from)) {
    throw new Error(`JC_K6_RAMP '${text}': use <from>:<to>:<time> with 0 < from <= to, e.g. 50:500:5m`);
  }
  return { from, to, time: parts[2], seconds: secondsOf(parts[2], 'JC_K6_RAMP') };
}

const RAMP = rampOf(__ENV.JC_K6_RAMP);
const RATE = RAMP ? RAMP.to : Number(__ENV.JC_K6_RATE || 100);
// A request takes about 0.3 s, so the peak rate needs 0.3 VUs per request a second; twice that.
const VUS = Number(__ENV.JC_K6_VUS || Math.max(60, Math.ceil(RATE * 0.6)));
const WS_SUBS = Number(__ENV.JC_K6_WS_SUBS || 10);
const LIST = __ENV.JC_K6_REQUESTS;
if (!LIST) {
  throw new Error('set JC_K6_REQUESTS to the request list (tasks/evidence/T-4714/requests-*.tsv)');
}

const parsed = new SharedArray('requests', () => [parseRequests(open(LIST))]);
const HTTP = parsed[0].requests.filter((r) => r.kind !== 'ws');
const SOCKETS = parsed[0].requests.filter((r) => r.kind === 'ws');
if (HTTP.length === 0) {
  throw new Error(`${LIST}: no HTTP request to send`);
}
const KINDS = [...new Set(HTTP.map((r) => r.kind))];
// The answers the summary breaks down per kind: which layer refused (the edge's 429, a 5xx of
// the gateway or the broker, 0 for a timeout or a reset).
const STATUSES = ['0', '200', '400', '404', '429', '500', '501', '502', '503', '504'];
const MCP_BODY = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' });

const wsOpened = new Rate('ws_opened');
const wsSubscribed = new Rate('ws_subscribed');
const wsMessages = new Counter('ws_messages');

const hold = secondsOf(DURATION, 'JC_K6_DURATION');
// The whole run, ramp included, which the per-kind table divides its counts by.
const seconds = hold + (RAMP ? RAMP.seconds : 0);
const abort = (threshold) => ({ threshold, abortOnFail: true, delayAbortEval: '1m' });

const thresholds = {
  http_req_failed: ['rate<0.01', abort('rate<0.02')],
  http_req_duration: [abort('p(95)<2000')],
  dropped_iterations: ['count<1'],
  checks: ['rate>0.99'],
};
for (const kind of KINDS) {
  thresholds[`http_req_duration{kind:${kind}}`] = [`p(95)<${P95}`];
  // listed so the summary carries each kind's count and errors, never a gate
  thresholds[`http_reqs{kind:${kind}}`] = ['count>=0'];
  thresholds[`http_req_failed{kind:${kind}}`] = ['rate>=0'];
  for (const status of STATUSES) {
    thresholds[`http_reqs{kind:${kind},status:${status}}`] = ['count>=0'];
  }
}
const scenarios = {
  mix: RAMP
    ? {
      executor: 'ramping-arrival-rate',
      startRate: RAMP.from,
      timeUnit: '1s',
      stages: [{ target: RAMP.to, duration: RAMP.time }, { target: RAMP.to, duration: DURATION }],
      preAllocatedVUs: VUS,
      maxVUs: VUS * 4,
      exec: 'mix',
    }
    : {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: VUS,
      maxVUs: VUS * 4,
      exec: 'mix',
    },
};
if (SOCKETS.length > 0 && WS_SUBS > 0) {
  scenarios.subscribers = {
    executor: 'per-vu-iterations',
    vus: WS_SUBS,
    iterations: 1,
    maxDuration: `${seconds + 30}s`,
    exec: 'subscriber',
  };
  thresholds.ws_opened = ['rate==1'];
  thresholds.ws_subscribed = ['rate==1'];
}

export const options = {
  scenarios,
  thresholds,
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
  discardResponseBodies: true,
};

// Each iteration takes the next request of the list, so all of them run at the same share.
export function mix() {
  const request = HTTP[exec.scenario.iterationInTest % HTTP.length];
  const params = { tags: { kind: request.kind }, timeout: '10s' };
  let answer;
  if (request.method === 'POST') {
    params.headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream' };
    answer = http.post(request.url, request.body || MCP_BODY, params);
  } else {
    answer = http.get(request.url, params);
  }
  check(answer, { [`${request.kind} answered 2xx`]: (r) => r.status >= 200 && r.status < 300 });
}

// One subscriber: open, subscribe to every type the Endpoint serves, hold for the run.
export function subscriber() {
  const target = SOCKETS[(exec.vu.idInTest - 1) % SOCKETS.length];
  const socket = new WebSocket(target.url, ['ngsi-ld-ws']);
  let opened = false;
  let answered = false;
  socket.onopen = () => {
    opened = true;
    socket.send(JSON.stringify({
      metadata: { 'x-op': 'x-subscribe', 'x-id': 'load' },
      body: { type: 'Subscription', entities: [{ type: '*' }] },
    }));
    setTimeout(() => socket.close(), seconds * 1000);
  };
  socket.onmessage = (event) => {
    wsMessages.add(1);
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    const meta = message.metadata || {};
    if (meta['x-op'] === 'x-result' && meta['x-id'] === 'load' && !answered) {
      answered = true;
      const status = Number(meta['x-status']);
      wsSubscribed.add(status >= 200 && status < 300, { url: target.url });
    }
  };
  socket.onclose = () => {
    wsOpened.add(opened, { url: target.url });
    if (opened && !answered) {
      wsSubscribed.add(false, { url: target.url });
    }
  };
  socket.onerror = () => {
    if (!opened) {
      socket.close();
    }
  };
}

function value(data, name, stat) {
  const metric = data.metrics[name];
  return metric && metric.values[stat] !== undefined ? metric.values[stat] : NaN;
}

// The per-kind table the task asks for: requests, rate reached, p50/p95/p99 and errors.
export function kindTable(data) {
  const rows = ['kind\trequests\treq/s\tp50 ms\tp95 ms\tp99 ms\terrors %\tstatuses'];
  const kinds = Object.keys(data.metrics)
    .map((name) => /^http_reqs\{kind:([^,}]+)\}$/.exec(name))
    .filter(Boolean)
    .map((m) => m[1])
    .sort();
  for (const kind of [...kinds, null]) {
    const tag = kind === null ? '' : `{kind:${kind}}`;
    const count = value(data, `http_reqs${tag}`, 'count');
    rows.push([
      kind === null ? 'all' : kind,
      count,
      (count / seconds).toFixed(1),
      value(data, `http_req_duration${tag}`, 'p(50)').toFixed(0),
      value(data, `http_req_duration${tag}`, 'p(95)').toFixed(0),
      value(data, `http_req_duration${tag}`, 'p(99)').toFixed(0),
      (100 * value(data, `http_req_failed${tag}`, 'rate')).toFixed(2),
      kind === null ? '' : STATUSES
        .map((status) => [status, value(data, `http_reqs{kind:${kind},status:${status}}`, 'count')])
        .filter(([, n]) => n > 0)
        .map(([status, n]) => `${status}:${n}`)
        .join(' '),
    ].join('\t'));
  }
  return rows.join('\n');
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const failed = Object.entries(data.metrics)
    .filter(([, m]) => m.thresholds && Object.values(m.thresholds).some((t) => !t.ok))
    .map(([name]) => name);
  const head = `k6 API mix (T-4714, T-4754): ${HTTP.length} requests (${parsed[0].skipped.length} skipped), ` +
    `${RAMP ? `${RAMP.from}→${RAMP.to}/s over ${RAMP.time}, then ` : ''}${RATE}/s for ${DURATION}, ` +
    `p95 budget ${P95} ms per kind, ${SOCKETS.length ? WS_SUBS : 0} WebSocket subscribers\n` +
    `dropped iterations: ${value(data, 'dropped_iterations', 'count') || 0}; ` +
    `ws opened ${value(data, 'ws_opened', 'rate')}, subscribed ${value(data, 'ws_subscribed', 'rate')}\n` +
    `thresholds: ${failed.length ? `RED ${failed.join(', ')}` : 'all green'}\n`;
  const text = `${head}\n${kindTable(data)}\n`;
  return { stdout: text, [`${dir}/summary.txt`]: text, [`${dir}/summary.json`]: JSON.stringify(data, null, 2) };
}

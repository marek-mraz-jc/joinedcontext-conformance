// Capacity (T-5900): where one deployment saturates, per standard and mixed. An open model
// (constant arrival rate) climbs in steps; each step is judged alone, and the ceiling is the
// highest step that held p95 under the budget, errors under the budget and dropped arrivals under
// 0.1 %. Anonymous only: the script sends no token.
//
//   JC_K6_REQUESTS  the list (requests.js); request i of the run is row i of the list, wrapping,
//                   so a list longer than the run repeats nothing (distinct load) and a short one
//                   (make_requests.py's catalogue mix) repeats like public traffic does
//   JC_K6_KINDS     the kinds to send, e.g. odata or ngsi,geo,temporal (default: every kind of
//                   the list); one standard per run gives its own ceiling
//   JC_K6_STEPS     the arrival rates, rising (default 250,500,1000,2000,4000,7000,10000)
//   JC_K6_STEP      each step's length (default 1m)
//   JC_K6_P95_MS    p95 budget per step (default 500)
//   JC_K6_ERRORS    error budget per step as a fraction (default 0.001)
//   JC_K6_FLOOR     the rate that must hold (the release gate's rate, T-5899): every step up to it
//                   is a threshold, so the run goes red (exit 99) when the ceiling is under it;
//                   steps above it are measured, never red (default 0: nothing must hold)
//   JC_K6_ABORT_MS  a step whose p95 passes this aborts the run after 30 s (default 2000), so a
//                   saturated stack is not kept saturated for the remaining steps
//   JC_K6_MAX_VUS   VU ceiling (default 2 x the top step): a run out of VUs drops arrivals, which
//                   counts against the step, never silently
//   JC_K6_MCP_VERSION  the MCP-Protocol-Version a POST row sends when its body names none
//                   (default 2026-07-28)
//   JC_REPORTS_DIR  where capacity.json goes (default .)
import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { parseRequests } from './requests.js';

const MCP_LIST = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' });
const MCP_VERSION = __ENV.JC_K6_MCP_VERSION || '2026-07-28';

function secondsOf(text, name) {
  const m = /^(\d+)(s|m)$/.exec(text);
  if (!m) {
    throw new Error(`${name} '${text}': use <n>s or <n>m`);
  }
  return Number(m[1]) * (m[2] === 'm' ? 60 : 1);
}

// The steps, strictly rising positive integers.
export function stepsOf(text) {
  const steps = text.split(',').map((s) => Number(s.trim()));
  steps.forEach((s, i) => {
    if (!Number.isInteger(s) || s <= 0 || (i > 0 && s <= steps[i - 1])) {
      throw new Error(`JC_K6_STEPS '${text}': rising positive integers, e.g. 250,500,1000`);
    }
  });
  return steps;
}

// The highest step that held, from rows {rate, p95, errors, dropped, sent}; 0 when none did.
// A step after a failed one does not count: a stack that recovers at a higher rate was measured
// on a different state (cold caches, a restart), not a higher capacity.
export function ceilingOf(rows, p95Ms, errorBudget) {
  let ceiling = 0;
  for (const row of rows) {
    const held = row.sent > 0 && row.p95 < p95Ms && row.errors < errorBudget && row.dropped < 0.001 * row.rate * row.seconds;
    if (!held) {
      break;
    }
    ceiling = row.rate;
  }
  return ceiling;
}

const LIST = __ENV.JC_K6_REQUESTS;
if (!LIST) {
  throw new Error('set JC_K6_REQUESTS to the request list (make_requests.py, or a distinct list)');
}
const STEPS = stepsOf(__ENV.JC_K6_STEPS || '250,500,1000,2000,4000,7000,10000');
const STEP = __ENV.JC_K6_STEP || '1m';
const STEP_S = secondsOf(STEP, 'JC_K6_STEP');
const P95 = Number(__ENV.JC_K6_P95_MS || 500);
const ERRORS = Number(__ENV.JC_K6_ERRORS || 0.001);
const FLOOR = Number(__ENV.JC_K6_FLOOR || 0);
const ABORT = Number(__ENV.JC_K6_ABORT_MS || 2000);
const WANT = (__ENV.JC_K6_KINDS || '').split(',').map((k) => k.trim()).filter(Boolean);

// The list is parsed once, in the first VU's init, never once per VU: a distinct list holds
// hundreds of thousands of rows.
function wanted() {
  const all = parseRequests(open(LIST)).requests.filter((r) => r.kind !== 'ws');
  const unknown = WANT.filter((k) => !all.some((r) => r.kind === k));
  if (unknown.length > 0) {
    throw new Error(`JC_K6_KINDS: the list has no ${unknown.join(', ')} request`);
  }
  return all.filter((r) => WANT.length === 0 || WANT.includes(r.kind));
}
const ROWS = new SharedArray('capacity', wanted);
if (ROWS.length === 0) {
  throw new Error(`${LIST}: no HTTP request to send`);
}
const KINDS = [...new SharedArray('capacity-kinds', () => [...new Set(wanted().map((r) => r.kind))])];

const scenarios = {};
const thresholds = {};
STEPS.forEach((rate, i) => {
  const name = `s${rate}`;
  scenarios[name] = {
    executor: 'constant-arrival-rate',
    rate,
    timeUnit: '1s',
    duration: STEP,
    startTime: `${i * STEP_S}s`,
    preAllocatedVUs: Math.max(10, Math.ceil(rate / 10)),
    maxVUs: Math.max(10, Math.ceil(rate / 10), Number(__ENV.JC_K6_MAX_VUS || 2 * STEPS[STEPS.length - 1])),
    gracefulStop: '5s',
    tags: { step: String(rate) },
  };
  // Every step gets its numbers into the summary; a step up to the floor must also hold.
  const must = rate <= FLOOR;
  thresholds[`http_req_duration{step:${rate}}`] = [
    must ? `p(95)<${P95}` : 'p(95)>=0',
    { threshold: `p(95)<${ABORT}`, abortOnFail: true, delayAbortEval: '30s' },
  ];
  thresholds[`http_req_failed{step:${rate}}`] = [must ? `rate<${ERRORS}` : 'rate>=0'];
  thresholds[`dropped_iterations{scenario:${name}}`] = [must ? `count<${Math.ceil(0.001 * rate * STEP_S)}` : 'count>=0'];
  thresholds[`http_reqs{step:${rate}}`] = ['count>=0'];
  for (const kind of KINDS) {
    thresholds[`http_req_duration{step:${rate},kind:${kind}}`] = ['p(95)>=0'];
    thresholds[`http_req_failed{step:${rate},kind:${kind}}`] = ['rate>=0'];
    thresholds[`http_reqs{step:${rate},kind:${kind}}`] = ['count>=0'];
  }
});

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
  scenarios,
  thresholds,
};

export default function () {
  const r = ROWS[exec.scenario.iterationInTest % ROWS.length];
  const tags = { kind: r.kind };
  if (r.method === 'POST') {
    // MCP streamable HTTP (2026-07-28) names the method, the tool and the protocol version in
    // headers beside the body; a list row may carry a tools/call (make_requests.py, the rig).
    const body = r.body || MCP_LIST;
    const call = JSON.parse(body);
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
      'MCP-Protocol-Version': ((call.params || {})._meta || {})['io.modelcontextprotocol/protocolVersion'] || MCP_VERSION,
      'Mcp-Method': call.method,
    };
    if (call.params && call.params.name) {
      headers['Mcp-Name'] = call.params.name;
    }
    http.post(r.url, body, { headers, tags });
  } else {
    http.get(r.url, { tags });
  }
}

function rowOf(metrics, rate, kind) {
  const sel = kind ? `step:${rate},kind:${kind}` : `step:${rate}`;
  const dur = metrics[`http_req_duration{${sel}}`];
  const failed = metrics[`http_req_failed{${sel}}`];
  // A trend has no count in the summary: the requests sent are http_reqs of the same tags.
  const reqs = (metrics[`http_reqs{${sel}}`] || { values: {} }).values.count || 0;
  const dropped = kind ? 0 : (metrics[`dropped_iterations{scenario:s${rate}}`] || { values: {} }).values.count || 0;
  return {
    rate,
    seconds: STEP_S,
    sent: reqs,
    achieved: Math.round(reqs / STEP_S),
    p50: dur ? Math.round(dur.values['p(50)']) : null,
    p95: dur ? Math.round(dur.values['p(95)']) : Infinity,
    p99: dur ? Math.round(dur.values['p(99)']) : null,
    errors: failed ? failed.values.rate : 1,
    dropped,
  };
}

export function handleSummary(data) {
  const m = data.metrics;
  const ran = STEPS.filter((rate) => (m[`http_reqs{step:${rate}}`] || { values: {} }).values.count > 0);
  const steps = ran.map((rate) => rowOf(m, rate));
  const perKind = {};
  for (const kind of KINDS) {
    const rows = ran.map((rate) => rowOf(m, rate, kind));
    perKind[kind] = { ceiling: ceilingOf(rows, P95, ERRORS), steps: rows };
  }
  const ceiling = ceilingOf(steps, P95, ERRORS);
  const report = {
    list: LIST, kinds: KINDS, rows: ROWS.length, step: STEP, p95_ms: P95, errors: ERRORS, floor: FLOOR,
    ceiling, steps, perKind,
  };
  const lines = [`k6 capacity (T-5900): ${ROWS.length} requests of ${KINDS.join(', ')}, steps of ${STEP}, p95 < ${P95} ms, errors < ${ERRORS * 100} %`];
  lines.push('step/s  achieved  p50 ms  p95 ms  p99 ms  errors %  dropped');
  for (const s of steps) {
    lines.push([s.rate, s.achieved, s.p50, s.p95, s.p99, (s.errors * 100).toFixed(2), s.dropped].map(String).map((v) => v.padStart(7)).join(' '));
  }
  for (const kind of KINDS) {
    lines.push(`ceiling ${kind}: ${perKind[kind].ceiling} req/s offered (mixed step)`);
  }
  lines.push(`CEILING ${ceiling} req/s${FLOOR ? ` (floor ${FLOOR}: ${ceiling >= FLOOR ? 'held' : 'NOT held'})` : ''}`);
  const dir = __ENV.JC_REPORTS_DIR || '.';
  return {
    stdout: `${lines.join('\n')}\n`,
    [`${dir}/capacity.json`]: JSON.stringify(report, null, 2),
    [`${dir}/summary.json`]: JSON.stringify(data),
  };
}

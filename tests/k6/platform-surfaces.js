// The platform's own read surfaces under a paced night load (T-4889): the Portal entity grid's
// three requests (a page, the page with its count, a filtered page; ui/src/components/entities/
// filters.ts fetchEntities) through one Endpoint, and an MCP tools/call query_entities on the same
// Endpoint's MCP server. Each runs as its own scenario at its own rate, judged per scenario.
// The Portal's live SSE and MQTT ingest are streams.py: k6 holds neither an SSE nor an MQTT stream.
//
//   ENDPOINT_URL=https://api.dev.joinedcontext.com/api/endpoint/<slug> ENTITY_TYPE=AirQualityObserved \
//   GATEWAY_TOKEN=<viewer token> JC_K6_SCRIPT=platform-surfaces.js jc-conformance k6
//
//   ENDPOINT_URL    the Endpoint the grid reads (required)
//   ENTITY_TYPE     the type the grid shows (required)
//   GRID_FILTER     the grid's q filter (default `level>=50`, the k6-surfaces.js seed's attribute)
//   MCP_URL         the Endpoint's MCP server; unset skips the MCP scenario
//   GATEWAY_TOKEN   a viewer's bearer token: read grants only (Security line of T-4889)
//   JC_K6_RATE      requests a second per scenario (default 5: four scenarios stay under the
//                   edge's per-identity limit on dev, so dev stays usable)
//   JC_K6_DURATION  how long (default 5m)
//   JC_K6_P95_MS    p95 budget per scenario (default 800), JC_K6_P99_MS p99 (default 2000)
import http from 'k6/http';
import { check } from 'k6';

const strip = (u) => (u || '').replace(/\/+$/, '');
const ENDPOINT = strip(__ENV.ENDPOINT_URL);
const TYPE = __ENV.ENTITY_TYPE || '';
if (!ENDPOINT || !TYPE) {
  throw new Error('set ENDPOINT_URL and ENTITY_TYPE');
}
const MCP_URL = strip(__ENV.MCP_URL);
const FILTER = __ENV.GRID_FILTER || 'level>=50';
const RATE = Number(__ENV.JC_K6_RATE || 5);
const DURATION = __ENV.JC_K6_DURATION || '5m';
const P95 = Number(__ENV.JC_K6_P95_MS || 800);
const P99 = Number(__ENV.JC_K6_P99_MS || 2000);
const AUTH = __ENV.GATEWAY_TOKEN ? { Authorization: `Bearer ${__ENV.GATEWAY_TOKEN}` } : {};

export const SHAPES = MCP_URL ? ['grid-page', 'grid-count', 'grid-filter', 'mcp'] : ['grid-page', 'grid-count', 'grid-filter'];

const scenarios = {};
const thresholds = { dropped_iterations: ['count<1'] };
for (const shape of SHAPES) {
  scenarios[shape] = {
    executor: 'constant-arrival-rate', rate: RATE, timeUnit: '1s', duration: DURATION,
    preAllocatedVUs: Math.max(5, RATE * 2), maxVUs: Math.max(20, RATE * 8), exec: 'read', env: { SHAPE: shape },
  };
  thresholds[`http_req_duration{shape:${shape}}`] = [`p(95)<${P95}`, `p(99)<${P99}`];
  thresholds[`http_req_failed{shape:${shape}}`] = ['rate<0.01'];
}
export const options = { scenarios, thresholds, summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'] };

// The request the grid (or the MCP client) sends for iteration `i`: method, url, body.
export function request(shape, i, endpoint = ENDPOINT, type = TYPE, filter = FILTER, mcp = MCP_URL) {
  const offset = (i % 20) * 50;
  const base = `${endpoint}/ngsi-ld/v1/entities?type=${encodeURIComponent(type)}&limit=50&options=keyValues`;
  switch (shape) {
    case 'grid-page': return ['GET', `${base}&offset=${offset}`];
    case 'grid-count': return ['GET', `${base}&offset=${offset}&count=true`];
    case 'grid-filter': return ['GET', `${base}&count=true&q=${encodeURIComponent(filter)}`];
    case 'mcp': return ['POST', mcp, JSON.stringify({
      jsonrpc: '2.0', id: i, method: 'tools/call',
      params: { name: 'query_entities', arguments: { type: [type], limit: 20 } },
    })];
    default: throw new Error(`unknown shape ${shape}`);
  }
}

export function read() {
  const shape = __ENV.SHAPE;
  const [method, url, body] = request(shape, __ITER);
  const headers = body
    ? { ...AUTH, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' }
    : { ...AUTH, Accept: 'application/ld+json' };
  const r = http.request(method, url, body || null, { headers, tags: { shape }, timeout: '10s' });
  check(r, {
    [`${shape} answered 200`]: (res) => res.status === 200,
    // the grid shows the count only when the header comes back (parseResultsCount)
    [`${shape} carries its count`]: (res) => !shape.startsWith('grid-') || shape === 'grid-page' || res.headers['Ngsild-Results-Count'] !== undefined,
    [`${shape} is no MCP refusal`]: (res) => shape !== 'mcp' || !String(res.body).includes('"isError":true'),
  });
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const rows = ['shape\tp95 ms\tp99 ms\terrors %\tverdict'];
  for (const shape of SHAPES) {
    const d = data.metrics[`http_req_duration{shape:${shape}}`];
    const f = data.metrics[`http_req_failed{shape:${shape}}`];
    const ok = [d, f].every((m) => m && Object.values(m.thresholds || {}).every((t) => t.ok));
    rows.push([shape, d ? d.values['p(95)'].toFixed(0) : '', d ? d.values['p(99)'].toFixed(0) : '',
      f ? (100 * f.values.rate).toFixed(2) : '', ok ? 'ok' : 'FAIL'].join('\t'));
  }
  const text = `k6 platform surfaces (T-4889): ${RATE}/s per shape for ${DURATION}, p95 < ${P95} ms, p99 < ${P99} ms\n${rows.join('\n')}\n`;
  return { stdout: text, [`${dir}/platform-surfaces.txt`]: text, [`${dir}/platform-surfaces.json`]: JSON.stringify(data, null, 2) };
}

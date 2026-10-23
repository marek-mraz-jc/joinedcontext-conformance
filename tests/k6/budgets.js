// The performance budgets the gateway must meet (TS-22, EP-28, OPS-18) and the report they produce.
// Shared by the benchmark and by selftest.js, which feeds them synthetic samples: a budget nobody
// can fail is worth nothing, and a budget proven only on a copy of itself proves nothing.

export const OVERHEAD_MS = 'ep28_gateway_overhead_ms';
export const GATEWAY_MS = 'ops18_gateway_read_ms';
export const BROKER_MS = 'ep28_broker_read_ms';
export const GATEWAY_READS = 'ts22_gateway_reads';
export const RSS_MB = 'ts22_gateway_rss_mb';
export const MEMORY_GROWTH = 'ts22_memory_growth';
// the measured slope itself, reported so a human reads the number the gate used;
// handleSummary runs outside the VU context, so it can only see what a metric carries
export const RSS_SLOPE_PCT = 'ts22_rss_slope_pct';
export const EXPORT_TTFB_MS = 'ep44_export_ttfb_ms';
export const EXPORT_MS = 'ep44_export_ms';
export const EXPORT_ROWS = 'ep41_export_rows';
export const EXPORT_TIMEOUTS = 'ep44_export_timeouts';
export const ENDPOINT_OVERHEAD_MS = 'ep28_endpoint_overhead_ms';
export const ENDPOINT_MS = 'ts05_endpoint_read_ms';
export const ENDPOINT_READS = 'ts05_endpoint_reads';
export const CSV_TTFB_MS = 'ep05_csv_ttfb_ms';
// answers that arrived without the standard RateLimit field on an endpoint that declares a limit
export const LIMIT_HEADERS_MISSING = 'ep20_limit_headers_missing';
// 429s: none while the offered rate stays under the endpoint's limit, at least one when a
// burst deliberately exceeds it. A limiter that never sheds is a limiter nobody proved.
export const LIMIT_SHED = 'ep20_limit_shed';

// '90s', '5m', '1h' -> seconds. Anything else is a configuration error, not a slow system.
export function durationSeconds(duration) {
  const match = /^(\d+)(s|m|h)$/.exec(duration);
  if (!match) throw new Error(`unsupported duration ${duration}, use e.g. 30s, 5m or 1h`);
  return Number(match[1]) * { s: 1, m: 60, h: 3600 }[match[2]];
}

export function budgets({ rate, duration, measureOverhead }) {
  const seconds = durationSeconds(duration);
  return {
    // EP-28: p99 gateway overhead <= 5.0 ms for cached policy evaluations
    ...(measureOverhead ? { [OVERHEAD_MS]: ['p(99)<5'] } : {}),
    // OPS-18: p95 ingress latency <= 50 ms for cached entity reads
    [GATEWAY_MS]: ['p(95)<50'],
    // TS-22: 5 000 req/s per replica sustained. The counter, not its rate: k6 divides a metric
    // rate by the whole run including setup and graceful stop, which understates the plateau.
    [GATEWAY_READS]: [`count>=${Math.floor(rate * seconds * 0.95)}`],
    // requests the arrival rate could not start at all: the system did not keep up
    dropped_iterations: ['count<1'],
    http_req_failed: ['rate<0.001'],
    // every read answered 200 with the entity that was asked for
    checks: ['rate==1.0'],
  };
}

export function summarize(data, header) {
  const lines = [header];
  for (const [name, metric] of Object.entries(data.metrics)) {
    for (const [threshold, result] of Object.entries(metric.thresholds || {})) {
      lines.push(`${result.ok ? 'PASS' : 'FAIL'} ${name} ${threshold}`);
    }
  }
  return `${lines.join('\n')}\n`;
}

// Least-squares slope of [{t, rss}] (t in seconds, rss in MB) expressed as a percentage
// of the mean rss per minute: ((d_rss / d_t) * 60 / mean_rss) * 100.
// Returns 0 for fewer than two samples or a zero mean.
export function slopePercent(samples) {
  if (!samples || samples.length < 2) return 0;
  const n = samples.length;
  let sumT = 0;
  let sumRss = 0;
  for (let i = 0; i < n; i++) {
    sumT += samples[i].t;
    sumRss += samples[i].rss;
  }
  const meanT = sumT / n;
  const meanRss = sumRss / n;
  if (meanRss === 0) return 0;

  let numerator = 0;
  let denominator = 0;
  for (let i = 0; i < n; i++) {
    const dt = samples[i].t - meanT;
    const dr = samples[i].rss - meanRss;
    numerator += dt * dr;
    denominator += dt * dt;
  }
  if (denominator === 0) return 0;
  const slopePerSecond = numerator / denominator;
  const slopePerMinute = slopePerSecond * 60;
  return (slopePerMinute / meanRss) * 100;
}

export function enduranceBudgets({ rate, duration }) {
  const seconds = durationSeconds(duration);
  return {
    http_req_failed: ['rate<0.0001'],
    checks: ['rate==1.0'],
    dropped_iterations: ['count<1'],
    [GATEWAY_MS]: ['p(95)<50'],
    [GATEWAY_READS]: [`count>=${Math.floor(rate * seconds * 0.95)}`],
    [MEMORY_GROWTH]: ['count<1'],
  };
}

/**
 * What one endpoint's representations must hold under sustained load (EP-05, EP-20, EP-28, TS-05).
 *
 * The 5 ms is the gateway's own overhead, paired against the broker on the same entity, which
 * is what EP-28 budgets: a total read latency of 5 ms would be a budget on the broker, the
 * network and the dataset size rather than on anything this platform controls. The total read
 * keeps the OPS-18 ingress budget, and a `file.csv` download is budgeted on its first byte,
 * because the rest of it is the size of the dataset.
 */
export function endpointBudgets({ rate, duration, measureOverhead, proveRateLimit }) {
  const seconds = durationSeconds(duration);
  return {
    // EP-28: p99 gateway overhead <= 5.0 ms on the endpoint surface, cached policy evaluation
    ...(measureOverhead ? { [ENDPOINT_OVERHEAD_MS]: ['p(99)<5'] } : {}),
    // OPS-18: p95 ingress latency <= 50 ms for a cached read
    [ENDPOINT_MS]: ['p(95)<50'],
    // EP-05: the download starts answering within a second, whatever its total size
    [CSV_TTFB_MS]: ['p(95)<1000'],
    [ENDPOINT_READS]: [`count>=${Math.floor(rate * seconds * 0.95)}`],
    dropped_iterations: ['count<1'],
    // the task's zero dropped connections: under 0.01% of the sustained phase, and the burst
    // phase is excluded because its 429s are the point rather than a failure
    'http_req_failed{phase:sustained}': ['rate<0.0001'],
    [LIMIT_HEADERS_MISSING]: ['count<1'],
    // EP-20: a limit that a burst cannot reach was never enforced
    ...(proveRateLimit ? { [LIMIT_SHED]: ['count>=1'] } : { [LIMIT_SHED]: ['count<1'] }),
    checks: ['rate==1.0'],
  };
}

export function exportBudgets({ rows, streams }) {
  return {
    [EXPORT_TTFB_MS]: ['p(95)<1000'],
    [EXPORT_MS]: ['max<300000'],
    [EXPORT_TIMEOUTS]: ['count<1'],
    [EXPORT_ROWS]: [`count>=${rows * streams}`],
    http_req_failed: ['rate<0.001'],
    checks: ['rate==1.0'],
  };
}

// T-5124: the fair-share verdict of fair-share.js. Each class's p95 under the anonymous flood
// against its own p95 before it: within `tolerance` (0.2 is 20 %) or the run fails. `classes`
// names the Trend pairs, e.g. { write: ['t5124_write_ms_base', 't5124_write_ms_flood'] }. A class
// without samples in either phase fails too: a verdict on nothing measured proves nothing.
export function fairShare(metrics, classes, tolerance) {
  const p95 = (name) => metrics[name] && metrics[name].values && metrics[name].values['p(95)'];
  const lines = [];
  let ok = true;
  for (const [label, [base, flood]] of Object.entries(classes)) {
    const before = p95(base);
    const during = p95(flood);
    const pass = before > 0 && during >= 0 && during <= before * (1 + tolerance);
    ok = ok && pass;
    const shown = (ms) => (typeof ms === 'number' ? `${ms.toFixed(1)} ms` : 'no samples');
    lines.push(`${pass ? 'PASS' : 'FAIL'} ${label} p95 ${shown(before)} before, ${shown(during)} under the flood (at most +${Math.round(tolerance * 100)} %)`);
  }
  return { ok, lines };
}

// T-5042: the milliseconds a `Server-Timing` header names for `metric` (`total;dur=12.5`), or
// undefined when the header or the metric is absent, so an answer from an older gateway is
// counted as unmeasured rather than as zero.
export function serverTiming(header, metric) {
  for (const entry of String(header || '').split(',')) {
    const [name, ...params] = entry.trim().split(';');
    if (name !== metric) continue;
    for (const param of params) {
      const [key, value] = param.trim().split('=');
      if (key === 'dur' && value !== undefined && value !== '' && Number.isFinite(Number(value))) {
        return Number(value);
      }
    }
  }
  return undefined;
}

// T-5042: the report's table of policy-budgets.js, one row per kind and level: p50/p95/p99 of the
// server time, its broker share and the round trip, from the t5042_* Trends' sub-metrics.
export function timingTable(metrics, kinds, levels) {
  const value = (name, stat) => {
    const values = (metrics[name] && metrics[name].values) || {};
    // k6 keeps an empty sub-metric as zeros: no samples is shown as unmeasured.
    if (!values.count) return '-';
    return stat === 'count' ? String(values.count) : values[stat].toFixed(1);
  };
  const rows = [
    '| kind | clients | n | server p50 | p95 | p99 | broker p50 | p95 | round trip p50 | p95 |',
    '|---|---|---|---|---|---|---|---|---|---|',
  ];
  for (const kind of kinds) {
    for (const level of levels) {
      const server = `t5042_server_ms{kind:${kind},level:${level}}`;
      const broker = `t5042_broker_ms{kind:${kind},level:${level}}`;
      const trip = `t5042_round_trip_ms{kind:${kind},level:${level}}`;
      // A kind the Endpoint does not serve was left out by setup.
      if (value(trip, 'count') === '-') continue;
      const cells = [value(trip, 'count'), value(server, 'p(50)'), value(server, 'p(95)'), value(server, 'p(99)'),
        value(broker, 'p(50)'), value(broker, 'p(95)'), value(trip, 'p(50)'), value(trip, 'p(95)')];
      rows.push(`| ${kind} | ${level} | ${cells.join(' | ')} |`);
    }
  }
  return rows.join('\n');
}

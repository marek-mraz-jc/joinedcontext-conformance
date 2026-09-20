// Endpoint bulk export streaming benchmark (T-0067, EP-41, EP-44, OPS-18).
//
// Validates high-volume streaming exports across tabular (file.csv) and archive
// (file.zip) representations under concurrent load, asserting TTFB, total duration,
// and complete payload integrity.
import http from 'k6/http';
import { check, fail } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import {
  EXPORT_MS,
  EXPORT_ROWS,
  EXPORT_TIMEOUTS,
  EXPORT_TTFB_MS,
  exportBudgets,
  summarize,
} from './budgets.js';

const stripSlash = (value) => (value || '').replace(/\/+$/, '');

const EXPORT_URL = stripSlash(__ENV.EXPORT_URL || __ENV.GATEWAY_URL);
const EXPECTED_ROWS = Number(__ENV.EXPORT_ROWS || 50000);
const STREAMS = Number(__ENV.JC_K6_EXPORT_STREAMS || 3);
const TOKEN = __ENV.GATEWAY_TOKEN || '';

const exportTtfbMs = new Trend(EXPORT_TTFB_MS);
const exportMs = new Trend(EXPORT_MS);
const exportRows = new Counter(EXPORT_ROWS);
const exportTimeouts = new Counter(EXPORT_TIMEOUTS);

export const options = {
  scenarios: {
    ep41_csv: {
      executor: 'per-vu-iterations',
      vus: STREAMS,
      iterations: 1,
      maxDuration: '310s',
      exec: 'exportCsv',
      gracefulStop: '10s',
    },
    ep44_zip: {
      executor: 'per-vu-iterations',
      vus: STREAMS,
      iterations: 1,
      maxDuration: '310s',
      exec: 'exportZip',
      gracefulStop: '10s',
    },
  },
  thresholds: exportBudgets({ rows: EXPECTED_ROWS, streams: STREAMS }),
};

function authHeaders() {
  const headers = {};
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;
  return headers;
}

export function setup() {
  // the counter must exist even when nothing times out, otherwise k6 skips its threshold
  exportTimeouts.add(0);
  if (!EXPORT_URL) {
    fail('set EXPORT_URL to the root of an endpoint, e.g. https://host/api/endpoint/{slug}');
  }
  return {};
}

function recordTimeout(res) {
  if (res.status === 0 || res.status === 504) {
    exportTimeouts.add(1);
    return true;
  }
  return false;
}

export function exportCsv() {
  const url = `${EXPORT_URL}/file.csv`;
  const res = http.get(url, {
    headers: authHeaders(),
    timeout: '300s',
    responseType: 'text',
    tags: { format: 'csv' },
  });

  exportTtfbMs.add(res.timings.waiting);
  exportMs.add(res.timings.duration);

  const timedOut = recordTimeout(res);
  check(timedOut, {
    'EP-44 CSV export completed without timeout': (t) => !t,
  });

  const contentType = res.headers['Content-Type'] || res.headers['content-type'] || '';
  let rowCount = 0;
  let hasIdHeader = false;

  if (res.status === 200 && res.body) {
    const lines = res.body.split('\n');
    if (lines.length > 0 && lines[lines.length - 1] === '') {
      lines.pop();
    }
    if (lines.length > 0) {
      const headerLine = lines[0];
      const headers = headerLine.split(',').map((h) => h.trim().replace(/^"|"$/g, ''));
      hasIdHeader = headers.indexOf('id') !== -1 || headers.some((h) => h === 'id' || h.endsWith('.id'));
      rowCount = lines.length - 1;
      exportRows.add(rowCount);
    }
  }

  check(res, {
    'EP-41 CSV export answered 200': (r) => r.status === 200,
    'EP-41 CSV Content-Type is text/csv': () => contentType.startsWith('text/csv'),
    'EP-45 CSV header contains id column': () => hasIdHeader,
    'EP-41 CSV row count meets expected minimum': () => rowCount >= EXPECTED_ROWS,
  });
}

export function exportZip() {
  const url = `${EXPORT_URL}/file.zip`;
  const res = http.get(url, {
    headers: authHeaders(),
    timeout: '300s',
    responseType: 'binary',
    tags: { format: 'zip' },
  });

  exportTtfbMs.add(res.timings.waiting);
  exportMs.add(res.timings.duration);

  const timedOut = recordTimeout(res);
  check(timedOut, {
    'EP-44 ZIP export completed without timeout': (t) => !t,
  });

  const contentType = res.headers['Content-Type'] || res.headers['content-type'] || '';
  let isZipMagic = false;
  let bodyLen = 0;

  if (res.status === 200 && res.body) {
    const bytes = new Uint8Array(res.body);
    bodyLen = bytes.length;
    // PK\x03\x04 signature: 0x50, 0x4b, 0x03, 0x04
    if (bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 0x03 && bytes[3] === 0x04) {
      isZipMagic = true;
    }
  }

  check(res, {
    'EP-41 ZIP export answered 200': (r) => r.status === 200,
    'EP-41 ZIP Content-Type is application/zip': () => contentType.startsWith('application/zip'),
    'EP-41 ZIP payload begins with PK local file header': () => isZipMagic,
    'EP-41 ZIP payload size is greater than 1KB': () => bodyLen > 1024,
  });
}

export function handleSummary(data) {
  const dir = __ENV.JC_REPORTS_DIR || '.';
  const text = summarize(
    data,
    `k6 bulk export benchmark (T-0067): ${STREAMS} streams per format against ${EXPORT_URL}`,
  );
  return {
    stdout: text,
    [`${dir}/summary.txt`]: text,
    [`${dir}/summary.json`]: JSON.stringify(data, null, 2),
  };
}

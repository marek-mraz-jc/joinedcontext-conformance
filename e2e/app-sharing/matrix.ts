/**
 * The App sharing matrix (T-4911): five copies of one App, one per visibility, opened by six kinds
 * of people at every door, against what Architecture/16 §1 and T-4910 say each may reach.
 *
 * Pure: the expectation table, the classification of what a door answered, the comparison and the
 * HTML report. The live journey (journeys/43-app-sharing-matrix.spec.ts) observes; this decides.
 */

export const VISIBILITIES = ['private', 'project', 'organization', 'roles', 'public'] as const;
export type Visibility = (typeof VISIBILITIES)[number];

export const CALLERS = ['anonymous', 'outsider', 'neighbour', 'member', 'author', 'bearer'] as const;
export type Caller = (typeof CALLERS)[number];

/** Who each caller is on dev (deployment components/context-gateway/seed/sharing/README.md). */
export const CALLER_TITLES: Record<Caller, string> = {
  anonymous: 'Anonymous visitor',
  outsider: 'Other organization (no binding)',
  neighbour: 'Same organization, other project',
  member: 'Project member',
  author: 'Owner (may propose Apps)',
  bearer: 'Role holder only',
};

/** The doors the journey measures live. */
export const DOORS = ['page', 'asset', 'services', 'me', 'list'] as const;
export type Door = (typeof DOORS)[number];

export const DOOR_TITLES: Record<Door, string> = {
  page: 'Apps host page (GET /)',
  asset: 'Asset (GET /probe.js)',
  services: 'Platform service (GET /api/services/jobs)',
  me: 'Portal API …/apps/{name}/me',
  list: 'Portal API app list',
};

/** The doors of the task the seeded plain-HTML App cannot exercise, and what covers them. */
export const NOT_MEASURED: ReadonlyArray<{ door: string; why: string }> = [
  {
    door: 'Functions (POST /api/functions/{fn})',
    why: 'The probe is a plain-HTML bundle with no functions; the route asks the same may_open as the page (portal apps/functions.rs), covered by portal tests/apps_functions_tests.rs.',
  },
  {
    door: 'WASM server App and pod Apps',
    why: 'Their requests go edge → pod or jc-wasm-host and never pass the Portal check (T-4922, parked in tasks/later); no wasm copy is seeded until it lands.',
  },
  {
    door: 'Preview URL',
    why: 'A preview exists only for an App with a pending Change; the seed publishes, so there is none to open.',
  },
  {
    door: 'Assistant links',
    why: 'The assistant links an App only on a model answer; model runs stay off under the dev spend guard.',
  },
];

/**
 * What a door answered, in words a reader of the report checks against the rule:
 * `open` the caller got the App; `login` the edge sent an anonymous visitor to sign in;
 * `refused` the 404 of an App never created; `forbidden` the roles 403 page;
 * `unauthorized` a 401 (a service or the Portal API without a person).
 */
export type Outcome = 'open' | 'login' | 'refused' | 'forbidden' | 'unauthorized' | `unexpected: ${string}`;

const ORGANIZATION: ReadonlySet<Caller> = new Set(['neighbour', 'member', 'author']);
const PROJECT: ReadonlySet<Caller> = new Set(['member', 'author']);

/** Whether the rule of `visibility` admits `caller` (Architecture/16 §1, T-4910 `admits`). */
export function mayOpen(visibility: Visibility, caller: Caller): boolean {
  switch (visibility) {
    case 'public':
      return true;
    case 'roles':
      return caller === 'bearer';
    case 'organization':
      return ORGANIZATION.has(caller);
    case 'project':
      return PROJECT.has(caller);
    case 'private':
      return caller === 'author';
  }
}

export function expected(visibility: Visibility, caller: Caller, door: Door): Outcome {
  const anonymous = caller === 'anonymous';
  if (door === 'me') {
    return anonymous ? 'unauthorized' : mayOpen(visibility, caller) ? 'open' : 'refused';
  }
  if (door === 'list') {
    // The Portal lists a project's Apps to whoever reads the project, whatever their visibility.
    return anonymous ? 'unauthorized' : PROJECT.has(caller) ? 'open' : 'refused';
  }
  if (anonymous && visibility !== 'public') return 'login';
  if (door === 'services') {
    // A service acts in a person's name: an anonymous visitor of a public App calls none.
    if (anonymous) return 'unauthorized';
    return mayOpen(visibility, caller) ? 'open' : 'refused';
  }
  if (mayOpen(visibility, caller)) return 'open';
  return visibility === 'roles' ? 'forbidden' : 'refused';
}

/** What the journey saw at one door. */
export interface Observation {
  status: number;
  /** Where a redirect pointed, or the URL the browser ended on. */
  location?: string;
  contentType?: string;
  /** The first bytes of the body, enough to tell a designed page from raw JSON. */
  bodyStart?: string;
  /** The page door: the probe rendered its own heading. */
  probeShown?: boolean;
  /** The list door: the App's name is in the answer. */
  listed?: boolean;
  /** The page door: what `#count` showed (`data-entity-count`, or why it has none). */
  data?: string;
  /** The page door: screenshot files, relative to the report. */
  shots?: string[];
}

function isLogin(location: string | undefined, idmHost: string): boolean {
  if (!location) return false;
  try {
    const url = new URL(location, 'https://placeholder.invalid');
    return url.host === idmHost || url.pathname.includes('/protocol/openid-connect/auth');
  } catch {
    return false;
  }
}

/** Whether a refusal is raw JSON rather than a page a person can read. */
export function isRawJson(observation: Observation): boolean {
  const start = (observation.bodyStart ?? '').trimStart();
  return (observation.contentType ?? '').includes('json') || start.startsWith('{') || start.startsWith('[');
}

export function classify(door: Door, observation: Observation, idmHost: string): Outcome {
  const { status } = observation;
  if (isLogin(observation.location, idmHost) && (status === 200 || (status >= 300 && status < 400))) {
    return 'login';
  }
  if (door === 'page') {
    if (status === 200) return observation.probeShown ? 'open' : 'unexpected: 200 without the probe';
    // A refusal is a page a person can read: never raw JSON, never the edge's plain-text 404 of a
    // host nobody routes (which is what an App that was never published gets).
    if ((status === 404 || status === 403) && isRawJson(observation)) {
      return `unexpected: ${status} as raw JSON, not a page`;
    }
    if ((status === 404 || status === 403) && !(observation.contentType ?? '').includes('text/html')) {
      return `unexpected: ${status} as ${observation.contentType ?? 'no content type'}, not a page`;
    }
  }
  if (door === 'list' && status === 200) return observation.listed ? 'open' : 'refused';
  switch (status) {
    case 200:
      return 'open';
    case 401:
      return 'unauthorized';
    case 403:
      return 'forbidden';
    case 404:
      return 'refused';
    default:
      return `unexpected: HTTP ${status}`;
  }
}

export interface Row {
  visibility: Visibility;
  caller: Caller;
  door: Door;
  expected: Outcome;
  actual: Outcome;
  match: boolean;
  observation: Observation;
}

export type Expect = (visibility: Visibility, caller: Caller, door: Door) => Outcome;

/**
 * The self-check (T-4911 Tests): `APP_SHARING_SELFCHECK=1` expects the public App to refuse the
 * anonymous visitor's page, which is wrong on purpose; a run with it must come out red.
 */
export function expectationFor(selfCheck: boolean): Expect {
  if (!selfCheck) return expected;
  return (visibility, caller, door) =>
    visibility === 'public' && caller === 'anonymous' && door === 'page' ? 'refused' : expected(visibility, caller, door);
}

export interface Measured {
  visibility: Visibility;
  caller: Caller;
  door: Door;
  observation: Observation;
}

export function compare(measured: Measured[], idmHost: string, expect: Expect = expected): Row[] {
  return measured.map(({ visibility, caller, door, observation }) => {
    const want = expect(visibility, caller, door);
    const actual = classify(door, observation, idmHost);
    return { visibility, caller, door, expected: want, actual, match: want === actual, observation };
  });
}

/** Every (visibility, caller, door) the matrix holds that the run did not measure. */
export function missing(measured: Measured[]): string[] {
  const seen = new Set(measured.map((m) => `${m.visibility}/${m.caller}/${m.door}`));
  const gaps: string[] = [];
  for (const visibility of VISIBILITIES) {
    for (const caller of CALLERS) {
      for (const door of DOORS) {
        if (!seen.has(`${visibility}/${caller}/${door}`)) gaps.push(`${visibility}/${caller}/${door}`);
      }
    }
  }
  return gaps;
}

const escape = (text: string): string =>
  text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c);

function cell(row: Row | undefined): string {
  if (!row) return '<td class="gap">not measured</td>';
  const shots = (row.observation.shots ?? [])
    .map((shot) => `<a href="${escape(shot)}">${escape(shot.match(/(\d+)\.png$/)?.[1] ?? 'shot')}</a>`)
    .join(' ');
  const data = row.observation.data ? `<span class="data">${escape(row.observation.data)}</span>` : '';
  const verdict = row.match
    ? `<span class="outcome">${escape(row.actual)}</span>`
    : `<span class="outcome">${escape(row.actual)}</span><span class="want">expected ${escape(row.expected)}</span>`;
  return `<td class="${row.match ? 'ok' : 'bad'}" title="HTTP ${row.observation.status}">${verdict}${data}${
    shots ? `<span class="shots">${shots}</span>` : ''
  }</td>`;
}

export interface ReportMeta {
  baseUrl: string;
  startedAt: string;
  selfCheck: boolean;
}

/** One self-contained HTML page: a verdict, then a table per door (visibility × caller). */
export function renderReport(rows: Row[], gaps: string[], meta: ReportMeta): string {
  const bad = rows.filter((row) => !row.match);
  const verdict = bad.length === 0 && gaps.length === 0;
  const index = new Map(rows.map((row) => [`${row.visibility}/${row.caller}/${row.door}`, row]));
  const tables = DOORS.map((door) => {
    const head = CALLERS.map((caller) => `<th scope="col">${escape(CALLER_TITLES[caller])}</th>`).join('');
    const body = VISIBILITIES.map(
      (visibility) =>
        `<tr><th scope="row">${visibility}</th>${CALLERS.map((caller) => cell(index.get(`${visibility}/${caller}/${door}`))).join('')}</tr>`,
    ).join('\n');
    return `<section><h2>${escape(DOOR_TITLES[door])}</h2><div class="scroll"><table><thead><tr><th scope="col">Visibility</th>${head}</tr></thead><tbody>${body}</tbody></table></div></section>`;
  }).join('\n');
  const unmeasured = NOT_MEASURED.map((item) => `<li><strong>${escape(item.door)}</strong>: ${escape(item.why)}</li>`).join('');
  const mismatches = bad
    .map(
      (row) =>
        `<li><code>${row.visibility} / ${row.caller} / ${row.door}</code>: expected ${escape(row.expected)}, got ${escape(row.actual)} (HTTP ${row.observation.status})</li>`,
    )
    .join('');
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>App sharing matrix</title>
<style>
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--ink:#1b2430;--muted:#5a6573;--line:#d9dee4;--ok:#e3f4e8;--okink:#17603a;--bad:#fde7e7;--badink:#9b1c1c;--gap:#f1f1f1;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#11161c;--card:#1a2129;--ink:#e7ecf1;--muted:#9aa6b2;--line:#2c3540;--ok:#163a26;--okink:#9fe0b8;--bad:#4a1d1d;--badink:#ffb4b4;--gap:#232a33}}
body{margin:0;background:var(--bg);color:var(--ink);padding:1.5rem 1rem}
main{max-width:80rem;margin:0 auto}
h1{margin:0 0 .25rem}.meta{color:var(--muted);margin:0 0 1rem}
.verdict{padding:1rem;border-radius:10px;font-weight:600;margin-bottom:1rem}
.verdict.ok{background:var(--ok);color:var(--okink)}.verdict.bad{background:var(--bad);color:var(--badink)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem;margin-bottom:1rem}
h2{font-size:1.1rem;margin:0 0 .75rem}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:48rem}
th,td{border:1px solid var(--line);padding:.45rem .5rem;text-align:left;vertical-align:top;font-size:.9rem}
thead th{background:var(--gap)}
td.ok{background:var(--ok);color:var(--okink)}td.bad{background:var(--bad);color:var(--badink)}td.gap{background:var(--gap);color:var(--muted)}
.outcome{font-weight:600;display:block}.want,.data,.shots{display:block;font-size:.8rem}
a{color:inherit}
</style></head><body><main>
<h1>App sharing matrix</h1>
<p class="meta">${escape(meta.baseUrl)} · ${escape(meta.startedAt)} · ${rows.length} cells measured${meta.selfCheck ? ' · self-check run: one expectation is wrong on purpose' : ''}</p>
<div class="verdict ${verdict ? 'ok' : 'bad'}" role="status">${
    verdict
      ? `All ${rows.length} cells match: every visibility opens for its own callers alone.`
      : `${bad.length} of ${rows.length} cells differ from the rule${gaps.length ? `, ${gaps.length} not measured` : ''}.`
  }</div>
${mismatches ? `<section><h2>Differences</h2><ul>${mismatches}</ul></section>` : ''}
${tables}
<section><h2>Doors not measured live</h2><ul>${unmeasured}</ul></section>
</main></body></html>
`;
}

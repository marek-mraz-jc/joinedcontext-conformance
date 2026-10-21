import { test, expect } from '@playwright/test';
import {
  CALLERS,
  DOORS,
  VISIBILITIES,
  classify,
  compare,
  expectationFor,
  expected,
  missing,
  renderReport,
  type Measured,
  type Observation,
} from './matrix.js';

/**
 * The App sharing matrix's own rules (T-4911), offline: the expectation table says what
 * Architecture/16 §1 says, a door's answer is read the way a person would read it, and a wrong
 * expectation turns the run red. No browser, no cluster.
 */
const IDM = 'idm.dev.joinedcontext.com';

/** What a door that behaves exactly as the rule says answers. */
function faithful(visibility: (typeof VISIBILITIES)[number], caller: (typeof CALLERS)[number], door: (typeof DOORS)[number]): Observation {
  switch (expected(visibility, caller, door)) {
    case 'open':
      return door === 'list' ? { status: 200, listed: true } : { status: 200, probeShown: true };
    case 'login':
      return { status: 302, location: `https://${IDM}/realms/dev/protocol/openid-connect/auth?client_id=x` };
    case 'refused':
      return { status: 404, contentType: 'text/html', bodyStart: '<!doctype html>' };
    case 'forbidden':
      return { status: 403, contentType: 'text/html', bodyStart: '<!doctype html>' };
    default:
      return { status: 401 };
  }
}

function everyCell(): Measured[] {
  return VISIBILITIES.flatMap((visibility) =>
    CALLERS.flatMap((caller) => DOORS.map((door) => ({ visibility, caller, door, observation: faithful(visibility, caller, door) }))),
  );
}

test.describe('App sharing matrix rules (T-4911)', () => {
  test('each visibility opens its page for its own callers alone', () => {
    const opens = (visibility: (typeof VISIBILITIES)[number]) =>
      CALLERS.filter((caller) => expected(visibility, caller, 'page') === 'open');
    expect(opens('public')).toEqual([...CALLERS]);
    expect(opens('organization')).toEqual(['neighbour', 'member', 'author']);
    expect(opens('project')).toEqual(['member', 'author']);
    expect(opens('private')).toEqual(['author']);
    expect(opens('roles')).toEqual(['bearer']);
  });

  test('an outsider learns nothing of a non-public App: the 404 of a name never created, never a 403', () => {
    for (const visibility of ['private', 'project', 'organization'] as const) {
      for (const door of ['page', 'asset', 'services', 'me'] as const) {
        expect(expected(visibility, 'outsider', door), `${visibility}/${door}`).toBe('refused');
      }
    }
    expect(expected('roles', 'member', 'page')).toBe('forbidden');
    expect(expected('roles', 'member', 'services')).toBe('refused');
  });

  test('anonymous visitors are sent to sign in, and call no service even of a public App', () => {
    expect(expected('project', 'anonymous', 'page')).toBe('login');
    expect(expected('public', 'anonymous', 'page')).toBe('open');
    expect(expected('public', 'anonymous', 'services')).toBe('unauthorized');
    expect(expected('public', 'anonymous', 'me')).toBe('unauthorized');
  });

  test('a door is read as a person reads it', () => {
    expect(classify('page', { status: 200, location: `https://${IDM}/realms/dev/login-actions/x` }, IDM)).toBe('login');
    expect(classify('page', { status: 200, probeShown: false }, IDM)).toBe('unexpected: 200 without the probe');
    expect(classify('page', { status: 404, contentType: 'application/json', bodyStart: '{"error":"x"}' }, IDM)).toBe(
      'unexpected: 404 as raw JSON, not a page',
    );
    expect(classify('page', { status: 404, contentType: 'text/plain; charset=utf-8', bodyStart: '404 page not found' }, IDM)).toBe(
      'unexpected: 404 as text/plain; charset=utf-8, not a page',
    );
    expect(classify('asset', { status: 404, contentType: 'application/json' }, IDM)).toBe('refused');
    expect(classify('list', { status: 200, listed: false }, IDM)).toBe('refused');
    expect(classify('services', { status: 500 }, IDM)).toBe('unexpected: HTTP 500');
  });

  test('a platform that keeps the rule matches every cell, and the report says so', () => {
    const measured = everyCell();
    const rows = compare(measured, IDM);
    expect(rows.filter((row) => !row.match)).toEqual([]);
    expect(missing(measured)).toEqual([]);
    const html = renderReport(rows, [], { baseUrl: 'https://dev.joinedcontext.com', startedAt: 'now', selfCheck: false });
    expect(html).toContain(`All ${VISIBILITIES.length * CALLERS.length * DOORS.length} cells match`);
  });

  test('self-check: a deliberately wrong expectation turns the run red', () => {
    const rows = compare(everyCell(), IDM, expectationFor(true));
    const bad = rows.filter((row) => !row.match);
    expect(bad.map((row) => `${row.visibility}/${row.caller}/${row.door}`)).toEqual(['public/anonymous/page']);
    expect(renderReport(rows, [], { baseUrl: 'x', startedAt: 'now', selfCheck: true })).toContain('1 of 150 cells differ');
  });

  test('the hole T-4910 closes reads red: a project App opening for a person of another project', () => {
    const measured = everyCell().map((cell) =>
      cell.visibility === 'project' && cell.caller === 'neighbour' && cell.door === 'page'
        ? { ...cell, observation: { status: 200, probeShown: true } }
        : cell,
    );
    const bad = compare(measured, IDM).filter((row) => !row.match);
    expect(bad).toHaveLength(1);
    expect(bad[0]).toMatchObject({ expected: 'refused', actual: 'open' });
  });

  test('a cell the run never measured is reported, never counted as a pass', () => {
    const measured = everyCell().filter((cell) => !(cell.visibility === 'private' && cell.caller === 'author'));
    expect(missing(measured)).toHaveLength(DOORS.length);
    const html = renderReport(compare(measured, IDM), missing(measured), { baseUrl: 'x', startedAt: 'now', selfCheck: false });
    expect(html).toContain(`${DOORS.length} not measured`);
  });
});

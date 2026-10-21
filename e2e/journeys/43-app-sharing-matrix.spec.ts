import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { APIResponse, BrowserContext, Page, Response } from '@playwright/test';
import { test, expect } from '@playwright/test';
import { signIn } from '../fixtures/portal.js';
import {
  CALLERS,
  VISIBILITIES,
  compare,
  expectationFor,
  missing,
  renderReport,
  type Caller,
  type Measured,
  type Observation,
  type Visibility,
} from '../app-sharing/matrix.js';

/**
 * Journey 43 (T-4911): the App sharing matrix, live. Five copies of one App, one per visibility
 * (project `sharing`, deployment seed/sharing), opened by six kinds of people at every door the
 * Portal answers for an App; each answer is checked against the rule of Architecture/16 §1 and the
 * whole goes into one HTML report with a 1440 and a 400 px screenshot of every page.
 *
 * Each person's cells are written to a file of the run as soon as they are measured, so one person's failed
 * sign-in costs only that person's column; the last test reads them all, writes the report and
 * fails on any cell that differs from the rule or was never measured.
 *
 * `. tests/dev.env.sh app-sharing` exports the people (APP_SHARING_<CALLER>_USER / _PASSWORD,
 * passwords read from the cluster's Secrets at run time, never written down).
 * `APP_SHARING_SELFCHECK=1` makes one expectation wrong on purpose: that run must be red.
 */
const reportRoot = path.join(process.env.JC_REPORTS_DIR || 'e2e/reports', 'app-sharing');
const project = process.env.APP_SHARING_PROJECT || 'sharing';
const selfCheck = process.env.APP_SHARING_SELFCHECK === '1';

/** Each person's cells, under the run's output folder, which Playwright empties when a run starts. */
function measuredDir(): string {
  return path.join(test.info().project.outputDir, 'app-sharing-measured');
}

function apex(baseURL: string | undefined): string {
  return process.env.PORTAL_APPS_DOMAIN || new URL(baseURL ?? 'https://dev.joinedcontext.com').hostname.replace(/^portal\./, '');
}

function idmHost(baseURL: string | undefined): string {
  return process.env.IDM_URL ? new URL(process.env.IDM_URL).host : `idm.${apex(baseURL)}`;
}

function person(caller: Caller): { user: string; pass: string } | null {
  const key = `APP_SHARING_${caller.toUpperCase()}`;
  const user = process.env[`${key}_USER`];
  const pass = process.env[`${key}_PASSWORD`];
  return user && pass ? { user, pass } : null;
}

async function fromResponse(response: APIResponse | Response | null, extra: Partial<Observation> = {}): Promise<Observation> {
  if (!response) return { status: 0, ...extra };
  const headers = response.headers();
  const body = await response.text().catch(() => '');
  return {
    status: response.status(),
    location: headers.location,
    contentType: headers['content-type'],
    bodyStart: body.slice(0, 200),
    ...extra,
  };
}

/** Keycloak's form, when the realm asks: the session of an earlier door usually spares it. */
async function passLogin(page: Page, origin: string, idm: string, who: { user: string; pass: string }): Promise<void> {
  if (new URL(page.url()).host !== idm) return;
  const userInput = page.getByLabel(/username or email|username|email/i);
  if (!(await userInput.isVisible().catch(() => false))) return;
  await userInput.fill(who.user);
  await page.getByLabel(/password/i).and(page.locator('input')).fill(who.pass);
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForURL((url) => url.origin === origin, { timeout: 45_000 });
}

async function openPage(
  page: Page,
  caller: Caller,
  visibility: Visibility,
  origin: string,
  idm: string,
  who: { user: string; pass: string } | null,
): Promise<Observation> {
  let response = await page.goto(`${origin}/`);
  if (who && new URL(page.url()).host === idm) {
    await passLogin(page, origin, idm, who);
    response = await page.goto(`${origin}/`);
  }
  const probeShown = await page
    .getByRole('heading', { level: 1, name: 'Sharing probe' })
    .isVisible()
    .catch(() => false);
  let data: string | undefined;
  if (probeShown) {
    const count = page.locator('#count[data-state]');
    await count.waitFor({ timeout: 15_000 }).catch(() => undefined);
    const state = await count.getAttribute('data-state').catch(() => null);
    const total = await count.getAttribute('data-entity-count').catch(() => null);
    const http = await count.getAttribute('data-http-status').catch(() => null);
    data = state === 'counted' ? `${total} entities` : `data: ${state ?? 'none'}${http ? ` (HTTP ${http})` : ''}`;
  }
  const shots: string[] = [];
  mkdirSync(path.join(reportRoot, 'shots'), { recursive: true });
  for (const [width, height] of [
    [1440, 900],
    [400, 860],
  ]) {
    await page.setViewportSize({ width, height });
    const file = `shots/${visibility}--${caller}--${width}.png`;
    await page.screenshot({ path: path.join(reportRoot, file), fullPage: true });
    shots.push(file);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  const observation = await fromResponse(response, { probeShown, data, shots });
  // The page ended where the browser stands: on the realm for a visitor sent to sign in.
  return { ...observation, location: page.url() };
}

async function measure(context: BrowserContext, caller: Caller, baseURL: string, who: { user: string; pass: string } | null): Promise<Measured[]> {
  const idm = idmHost(baseURL);
  const page = await context.newPage();
  const cells: Measured[] = [];
  if (who) {
    await page.goto('/');
    await signIn(page, who.user, who.pass, baseURL);
  }
  const listing = await context.request.get(`${baseURL}/api/v1/projects/${project}/apps`, { maxRedirects: 0 });
  const listed = listing.status() === 200 ? await listing.text() : '';
  const listObservation = await fromResponse(listing);
  for (const visibility of VISIBILITIES) {
    const name = `sharing-${visibility}`;
    const origin = `https://${name}.apps.${apex(baseURL)}`;
    const get = (url: string) => context.request.get(url, { maxRedirects: 0 });
    cells.push({ visibility, caller, door: 'page', observation: await openPage(page, caller, visibility, origin, idm, who) });
    cells.push({ visibility, caller, door: 'asset', observation: await fromResponse(await get(`${origin}/probe.js`)) });
    cells.push({ visibility, caller, door: 'services', observation: await fromResponse(await get(`${origin}/api/services/jobs`)) });
    cells.push({
      visibility,
      caller,
      door: 'me',
      observation: await fromResponse(await get(`${baseURL}/api/v1/projects/${project}/apps/${name}/me`)),
    });
    cells.push({ visibility, caller, door: 'list', observation: { ...listObservation, listed: listed.includes(`"${name}"`) } });
  }
  return cells;
}

test.describe('Journey 43: the App sharing matrix (T-4911, AP-92, T-4910)', () => {
  for (const caller of CALLERS) {
    test(`${caller} at every door of the five Apps`, async ({ browser, baseURL }) => {
      test.setTimeout(240_000);
      const who = caller === 'anonymous' ? null : person(caller);
      test.skip(
        caller !== 'anonymous' && !who,
        `APP_SHARING_${caller.toUpperCase()}_USER/_PASSWORD unset (. tests/dev.env.sh app-sharing); the report counts the column as not measured`,
      );
      const base = baseURL ?? 'https://dev.joinedcontext.com';
      const context = await browser.newContext({ baseURL: base, ignoreHTTPSErrors: true, locale: 'en-GB', viewport: { width: 1440, height: 900 } });
      try {
        const cells = await measure(context, caller, base, who);
        mkdirSync(measuredDir(), { recursive: true });
        writeFileSync(path.join(measuredDir(), `${caller}.json`), JSON.stringify(cells, null, 2));
      } finally {
        await context.close();
      }
    });
  }

  test('every cell matches the rule, and the report says so', async ({ baseURL }) => {
    mkdirSync(reportRoot, { recursive: true });
    mkdirSync(measuredDir(), { recursive: true });
    const measured: Measured[] = readdirSync(measuredDir())
      .filter((file) => file.endsWith('.json'))
      .flatMap((file) => JSON.parse(readFileSync(path.join(measuredDir(), file), 'utf8')) as Measured[]);
    const rows = compare(measured, idmHost(baseURL), expectationFor(selfCheck));
    const gaps = missing(measured);
    writeFileSync(path.join(reportRoot, 'results.json'), JSON.stringify({ rows, gaps }, null, 2));
    writeFileSync(
      path.join(reportRoot, 'report.html'),
      renderReport(rows, gaps, { baseUrl: baseURL ?? '', startedAt: new Date().toISOString(), selfCheck }),
    );
    const differences = rows
      .filter((row) => !row.match)
      .map((row) => `${row.visibility}/${row.caller}/${row.door}: expected ${row.expected}, got ${row.actual}`);
    expect(gaps, `cells never measured; report: ${path.join(reportRoot, 'report.html')}`).toEqual([]);
    expect(differences, `cells that differ from the rule; report: ${path.join(reportRoot, 'report.html')}`).toEqual([]);
  });
});

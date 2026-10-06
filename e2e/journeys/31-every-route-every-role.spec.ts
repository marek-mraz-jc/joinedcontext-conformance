import type { Page, TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 31 (T-3131): every Portal route, as each demo role, reads and writes nothing. A route
 * passes when it opens on a heading of its own, shows no error frame, threw nothing, got no 5xx
 * from the Portal's API, does not scroll sideways at a phone's width and axe finds nothing
 * serious or critical at WCAG 2.1 AA. A role the route is not for passes when the page says so
 * in its own words (a 403 or 404 from the API is the permission working, never a failure).
 *
 * Each role is one test that walks every route with soft assertions, so a run is the whole
 * route × role table: it is attached as `routes.json` and printed, one line per route.
 */
const PROJECT = process.env.PORTAL_PROJECT ?? 'helsinki';

/** The project's own pages: the sidebar's, and those reached from them. */
const PROJECT_PAGES = [
  'flows', 'spaces', 'endpoints', 'subscriptions', 'datasources', 'pipelines', 'dashboards', 'apps',
  'syncsources', 'assistant', 'workspaces', 'approvals', 'activity', 'policies', 'models', 'explore',
  'ckan', 'import', 'federation', 'shared', 'access',
];
const SETTINGS_TABS = ['general', 'members', 'roles', 'service-accounts', 'access', 'danger'];
const ORGANIZATION_TABS = [
  'settings', 'people', 'members', 'roles', 'groups', 'service-accounts', 'blueprints', 'agentprofiles',
  'dataspaceparticipants', 'environments', 'models', 'projects', 'applications', 'setup', 'health', 'endpoints',
];

const ROUTES: string[] = [
  ...PROJECT_PAGES.map((page) => `/projects/${PROJECT}/${page}`),
  ...SETTINGS_TABS.map((tab) => `/projects/${PROJECT}/settings/${tab}`),
  ...ORGANIZATION_TABS.map((tab) => `/organization/${tab}`),
  '/catalogue',
  '/endpoints',
];

const ROLES = [
  { role: 'steward', user: 'PORTAL_USER', password: 'PORTAL_PASSWORD' },
  { role: 'editor', user: 'PORTAL_EDITOR_USER', password: 'PORTAL_EDITOR_PASSWORD' },
  { role: 'approver', user: 'PORTAL_APPROVER_USER', password: 'PORTAL_APPROVER_PASSWORD' },
  { role: 'viewer', user: 'PORTAL_VIEWER_USER', password: 'PORTAL_VIEWER_PASSWORD' },
];

/** What a person reads on an error frame; a permission sentence is not one of them. */
const BROKEN = /something went wrong|could not be (loaded|read)|failed to (load|fetch)|unexpected error|internal server error/i;

interface Row {
  route: string;
  heading: string;
  problems: string[];
}

async function visit(page: Page, route: string): Promise<Row> {
  const problems: string[] = [];
  const thrown: string[] = [];
  const failed: string[] = [];
  const onError = (error: Error) => thrown.push(error.message.slice(0, 160));
  const onResponse = (response: { url(): string; status(): number }) => {
    if (response.status() >= 500 && new URL(response.url()).pathname.startsWith('/api/')) {
      failed.push(`${response.status()} ${new URL(response.url()).pathname}`);
    }
  };
  page.on('pageerror', onError);
  page.on('response', onResponse);
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${route}?lang=en`, { waitUntil: 'load' });
    const heading = page.getByRole('heading', { level: 1 }).first();
    const opened = await heading.waitFor({ state: 'visible', timeout: 20_000 }).then(() => true, () => false);
    // A page reads its data after its heading; the loading sentence leaves before the checks.
    await page.getByRole('status').filter({ hasText: /loading|načítava|lädt/i }).first()
      .waitFor({ state: 'hidden', timeout: 20_000 }).catch(() => {});
    const title = opened ? (await heading.innerText()).trim() : '';
    if (!opened) problems.push('no heading of its own');
    const alerts = await page.getByRole('alert').allInnerTexts();
    const broken = alerts.filter((text) => BROKEN.test(text));
    if (broken.length) problems.push(`error frame: ${broken[0].slice(0, 120)}`);
    if (thrown.length) problems.push(`threw: ${thrown[0]}`);
    if (failed.length) problems.push(`api ${failed[0]}`);
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    if (serious.length) problems.push(`axe ${serious.map((v) => v.id).join(', ')}`);
    await page.setViewportSize({ width: 375, height: 812 });
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (sideways > 1) problems.push(`scrolls sideways by ${sideways} px at 375`);
    return { route, heading: title, problems };
  } finally {
    page.off('pageerror', onError);
    page.off('response', onResponse);
  }
}

function report(testInfo: TestInfo, role: string, rows: Row[]): void {
  const lines = rows.map((row) => `${row.problems.length ? 'FAIL' : 'PASS'} ${role} ${row.route} ${row.heading ? `"${row.heading}"` : ''} ${row.problems.join('; ')}`);
  console.log(lines.join('\n'));
  void testInfo.attach(`routes-${role}.json`, { body: JSON.stringify(rows, null, 2), contentType: 'application/json' });
}

test.describe('Journey 31: every route, every role, nothing written (T-3131)', () => {
  test.setTimeout(30 * 60_000);
  for (const { role, user, password } of ROLES) {
    test(`${role}: every route opens, finished and readable`, async ({ page, baseURL }, testInfo) => {
      const name = process.env[user];
      const secret = process.env[password];
      test.skip(!name || !secret, `${user} and ${password} name the ${role}`);
      const portal = process.env.PORTAL_URL ?? baseURL ?? 'https://portal.dev.joinedcontext.com';
      await page.goto(portal);
      await signIn(page, name as string, secret as string, portal);
      const rows: Row[] = [];
      for (const route of ROUTES) {
        const row = await visit(page, new URL(route, portal).toString());
        rows.push({ ...row, route });
        expect.soft(row.problems, `${role} ${route}`).toEqual([]);
      }
      report(testInfo, role, rows);
    });
  }
});

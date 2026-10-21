import type { Page, TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 31 (T-3131, T-4887): every Portal route, as each demo role, reads and writes nothing. A
 * route passes when it opens on a heading of its own, shows no error frame, threw nothing, got no
 * 5xx from the Portal's API, does not scroll sideways at a phone's width and axe finds nothing
 * serious or critical at WCAG 2.1 AA. A role the route is not for passes when the page says so
 * in its own words (a 403 or 404 from the API is the permission working, never a failure).
 *
 * Each role is one test that walks every route with soft assertions, so a run is the whole
 * route × role table: it is attached as `routes.json` and printed, one line per route. The
 * anonymous test walks the public pages: the login theme, the catalogue, the MCP setup page, the
 * published App, the assistant widget, the CKAN catalogue and the landing site.
 *
 * The visual audit (T-4887): every visit keeps a full-page screenshot at 1440 px and at 400 px under
 * `JC_EVIDENCE_DIR` (default `<reports>/every-route`), `<role>/<route>-<width>.jpg`, and a
 * `grades-<role>.csv` row per route and width: broken (a failed check above), needs work (raw JSON,
 * a URN or an i18n key on screen, underlined link soup in a table, console errors, a missing
 * heading on a public page) or modern. The findings are listed beside the grade; only the checks
 * of the first paragraph fail the test. Requests stay under 150 a minute (the edge's rate).
 */
const PROJECT = process.env.PORTAL_PROJECT ?? 'helsinki';
const DOMAIN = process.env.JC_DOMAIN ?? 'dev.joinedcontext.com';
const EVIDENCE = process.env.JC_EVIDENCE_DIR ?? join(process.env.JC_REPORTS_DIR || 'e2e/reports', 'every-route');
const P = `/projects/${PROJECT}`;

/** The project's own pages: the sidebar's, and those reached from them. */
const PROJECT_PAGES = [
  'home', 'flows', 'spaces', 'endpoints', 'subscriptions', 'datasources', 'pipelines', 'dashboards', 'apps',
  'syncsources', 'csrs', 'assistant', 'workspaces', 'approvals', 'activity', 'policies', 'models', 'explore',
  'ckan', 'import', 'federation', 'shared', 'access', 'inbox', 'mcp', 'knowledge', 'knowledge/conversations',
  'spaces/upload', 'workspaces/new',
];
/** The sections whose create and edit forms open at their own address. */
const FORMS = ['spaces', 'endpoints', 'pipelines', 'policies', 'subscriptions', 'csrs', 'datasources', 'syncsources'];
const SETTINGS_TABS = ['general', 'members', 'roles', 'service-accounts', 'access', 'danger'];
const ORGANIZATION_TABS = [
  'settings', 'people', 'members', 'roles', 'groups', 'service-accounts', 'blueprints', 'agentprofiles',
  'dataspaceparticipants', 'environments', 'models', 'projects', 'applications', 'setup', 'health', 'endpoints',
];

const ROUTES: string[] = [
  '/',
  ...PROJECT_PAGES.map((page) => `${P}/${page}`),
  ...FORMS.map((section) => `${P}/${section}/new`),
  ...SETTINGS_TABS.map((tab) => `${P}/settings/${tab}`),
  ...ORGANIZATION_TABS.map((tab) => `/organization/${tab}`),
  '/organization/people/new',
  '/catalogue',
  '/endpoints',
  '/glossary',
  '/connectors',
  '/playground',
];

/** A list page and the first item link on it that opens one item, as a person finds it. */
const DETAILS: [string, RegExp][] = [
  ...[...FORMS, 'apps', 'dashboards', 'models', 'approvals'].map((section): [string, RegExp] => [
    `${P}/${section}`,
    new RegExp(`^${P}/${section}/(?!new$|complete$|upload$)[^/]+$`),
  ]),
  [`${P}/workspaces`, new RegExp(`^${P}/workspaces/[^/]+/compare$`)],
  ['/organization/people', /^\/organization\/people\/(?!new$)[^/]+$/],
  ['/catalogue', /^\/catalogue\/[^/]+$/],
];

/** The pages a person reads without signing in; a Portal route sends them to the login theme. */
const PUBLIC_PAGES: string[] = [
  `https://portal.${DOMAIN}/`,
  `https://portal.${DOMAIN}/catalogue`,
  `https://portal.${DOMAIN}/glossary`,
  `https://portal.${DOMAIN}/connect/${process.env.JC_CONNECT_SERVER ?? 'bbsk/otvorene-data'}`,
  `https://${process.env.JC_PUBLIC_APP ?? 'bbsk-demo'}.apps.${DOMAIN}/`,
  `https://assistant.${DOMAIN}/d/${process.env.JC_ASSISTANT_PUBLIC_ID ?? 'banskabystrica'}`,
  `https://opendata.${DOMAIN}/`,
  `https://opendata.${DOMAIN}/dataset/`,
  `https://${DOMAIN}/`,
];

const ROLES = [
  { role: 'admin', user: 'PORTAL_ADMIN_USER', password: 'PORTAL_ADMIN_PASSWORD' },
  { role: 'steward', user: 'PORTAL_USER', password: 'PORTAL_PASSWORD' },
  { role: 'editor', user: 'PORTAL_EDITOR_USER', password: 'PORTAL_EDITOR_PASSWORD' },
  { role: 'approver', user: 'PORTAL_APPROVER_USER', password: 'PORTAL_APPROVER_PASSWORD' },
  { role: 'viewer', user: 'PORTAL_VIEWER_USER', password: 'PORTAL_VIEWER_PASSWORD' },
];

/** What a person reads on an error frame; a permission sentence is not one of them. */
const BROKEN = /something went wrong|could not be (loaded|read)|failed to (load|fetch)|unexpected error|internal server error/i;
/** The checks a broken page fails; every other problem is "needs work". */
const BROKEN_PROBLEM = /^(no heading of its own|error frame|threw|api 5)/;

interface Row {
  route: string;
  heading: string;
  problems: string[];
  findings: string[];
  phone: string[];
  links: string[];
}

/** At most `limit` requests in any minute: a visit waits until the window has room. */
function pacer(page: Page, limit = 140): () => Promise<void> {
  const sent: number[] = [];
  page.context().on('request', () => sent.push(Date.now()));
  return async () => {
    for (;;) {
      while (sent.length && sent[0] < Date.now() - 60_000) sent.shift();
      if (sent.length < limit) return;
      await page.waitForTimeout(sent[0] + 60_001 - Date.now());
    }
  };
}

/** What a finished page never shows: read in the page, outside code blocks and editors. */
async function onScreen(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    const skip = 'pre, code, textarea, input, script, style, .cm-editor, [role="textbox"], [aria-hidden="true"]';
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let json = 0;
    let urn = 0;
    let key = 0;
    const keys: string[] = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      const text = (node.textContent ?? '').trim();
      if (!parent || !text || parent.closest(skip) || !parent.checkVisibility()) continue;
      if (/^[[{]\s*"[^"]+"\s*:/.test(text) && text.length > 20) json += 1;
      if (/^urn:ngsi-ld:/i.test(text) && parent.closest('td, [role="gridcell"], [role="cell"]')) urn += 1;
      if (/^[a-z][a-zA-Z0-9]*(\.[a-zA-Z0-9_]+){2,}$/.test(text) && !/\.(json|ya?ml|com|sk|fi|org|io)$/.test(text)) {
        key += 1;
        if (keys.length < 2) keys.push(text);
      }
    }
    if (json) found.push(`raw JSON in ${json} place(s)`);
    if (urn) found.push(`URN in ${urn} table cell(s)`);
    if (key) found.push(`i18n key on screen: ${keys.join(', ')}`);
    const underlined = Array.from(document.querySelectorAll('td a, [role="gridcell"] a'))
      .filter((link) => getComputedStyle(link).textDecorationLine.includes('underline')).length;
    if (underlined > 10) found.push(`${underlined} underlined links in table cells`);
    return found;
  });
}

async function visit(page: Page, route: string, shotPath: string, isPublic: boolean): Promise<Row> {
  const problems: string[] = [];
  const findings: string[] = [];
  const phone: string[] = [];
  const thrown: string[] = [];
  const failed: string[] = [];
  const consoleErrors: string[] = [];
  const onError = (error: Error) => thrown.push(error.message.slice(0, 160));
  const onResponse = (response: { url(): string; status(): number }) => {
    const url = new URL(response.url());
    if (response.status() >= 500 && (isPublic ? url.host.endsWith(DOMAIN) : url.pathname.startsWith('/api/'))) {
      failed.push(`${response.status()} ${url.pathname}`);
    }
  };
  const onConsole = (message: { type(): string; text(): string }) => {
    // A refused read logs a 401, 403 or 404: that is the permission working, as above.
    if (message.type() === 'error' && !/status of (401|403|404)\b/.test(message.text())) {
      consoleErrors.push(message.text().slice(0, 120));
    }
  };
  page.on('pageerror', onError);
  page.on('response', onResponse);
  page.on('console', onConsole);
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    const address = new URL(route);
    if (!isPublic) address.searchParams.set('lang', 'en');
    await page.goto(address.toString(), { waitUntil: 'load' });
    const heading = page.getByRole('heading', { level: 1 }).first();
    const opened = await heading.waitFor({ state: 'visible', timeout: isPublic ? 10_000 : 20_000 }).then(() => true, () => false);
    // A page reads its data after its heading; the loading sentence leaves before the checks.
    await page.getByRole('status').filter({ hasText: /loading|načítava|lädt/i }).first()
      .waitFor({ state: 'hidden', timeout: 20_000 }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => {});
    const title = opened ? (await heading.innerText()).trim() : '';
    if (!opened) (isPublic ? findings : problems).push('no heading of its own');
    const alerts = await page.getByRole('alert').allInnerTexts();
    const broken = alerts.filter((text) => BROKEN.test(text));
    if (broken.length) problems.push(`error frame: ${broken[0].slice(0, 120)}`);
    if (thrown.length) problems.push(`threw: ${thrown[0]}`);
    if (failed.length) problems.push(`api ${failed[0]}`);
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    const serious = axe.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
    if (serious.length) problems.push(`axe ${serious.map((v) => v.id).join(', ')}`);
    findings.push(...(await onScreen(page)));
    if (consoleErrors.length) findings.push(`${consoleErrors.length} console error(s): ${consoleErrors[0]}`);
    const links = await page.locator('main a[href]').evaluateAll((all) => all.map((link) => link.getAttribute('href') ?? ''))
      .catch(() => [] as string[]);
    await shot(page, `${shotPath}-1440.jpg`);
    await page.setViewportSize({ width: 400, height: 860 });
    await page.waitForTimeout(300);
    await shot(page, `${shotPath}-400.jpg`);
    await page.setViewportSize({ width: 375, height: 812 });
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (sideways > 1) phone.push(`scrolls sideways by ${sideways} px at 375`);
    return { route, heading: title, problems, findings, phone, links };
  } finally {
    page.off('pageerror', onError);
    page.off('response', onResponse);
    page.off('console', onConsole);
  }
}

/** A full-page picture, its first 8000 px only: a taller capture crashes the headless shell. */
async function shot(page: Page, path: string): Promise<void> {
  const { width, height } = await page.evaluate(() => ({ width: window.innerWidth, height: document.documentElement.scrollHeight }));
  await page.screenshot({ path, fullPage: true, type: 'jpeg', quality: 60, clip: { x: 0, y: 0, width, height: Math.min(height, 8000) } });
}

function slug(route: string): string {
  const url = new URL(route);
  const host = url.host.startsWith('portal.') ? '' : `${url.host.split('.')[0]}_`;
  return `${host}${url.pathname.replace(/^\/|\/$/g, '').replace(/[^a-zA-Z0-9-]+/g, '_') || 'root'}`;
}

function grade(problems: string[], findings: string[]): string {
  if (problems.some((problem) => BROKEN_PROBLEM.test(problem))) return 'broken';
  return problems.length || findings.length ? 'needs work' : 'modern';
}

const csv = (cell: string) => `"${cell.replace(/"/g, '""')}"`;

function report(testInfo: TestInfo, role: string, rows: Row[]): void {
  const lines = rows.map((row) => {
    const all = [...row.problems, ...row.phone];
    return `${all.length ? 'FAIL' : 'PASS'} ${role} ${row.route} ${row.heading ? `"${row.heading}"` : ''} ${all.join('; ')}`;
  });
  console.log(lines.join('\n'));
  const grades = rows.flatMap((row) => [
    [row.route, role, '1440', grade(row.problems, row.findings), [...row.problems, ...row.findings].join('; ')],
    [row.route, role, '400', grade(row.phone, []), row.phone.join('; ')],
  ].map((cells) => cells.map(csv).join(',')));
  writeFileSync(join(EVIDENCE, `grades-${role}.csv`), `route,role,width,grade,findings\n${grades.join('\n')}\n`);
  void testInfo.attach(`routes-${role}.json`, {
    body: JSON.stringify(rows.map(({ links: _links, ...row }) => row), null, 2),
    contentType: 'application/json',
  });
}

async function walk(start: Page, role: string, routes: string[], isPublic: boolean, rows: Row[]): Promise<Page> {
  let page = start;
  const wait = pacer(page);
  const dir = join(EVIDENCE, role);
  mkdirSync(dir, { recursive: true });
  for (const route of routes) {
    await wait();
    // A page that crashes or times out is that route's problem; the walk goes on in a new tab.
    const row = await visit(page, route, join(dir, slug(route)), isPublic).catch(async (error: Error) => {
      if (!page.isClosed()) await page.close().catch(() => {});
      page = await start.context().newPage();
      return { route, heading: '', problems: [`no heading of its own: ${error.message.split('\n')[0].slice(0, 120)}`], findings: [], phone: [], links: [] };
    });
    rows.push(row);
    appendFileSync(join(EVIDENCE, `progress-${role}.log`), `${row.route} ${[...row.problems, ...row.findings, ...row.phone].join('; ')}\n`);
    expect.soft([...row.problems, ...row.phone], `${role} ${route}`).toEqual([]);
  }
  return page;
}

test.describe('Journey 31: every route, every role, nothing written (T-3131, T-4887)', () => {
  test.setTimeout(90 * 60_000);
  for (const { role, user, password } of ROLES) {
    test(`${role}: every route opens, finished and readable`, async ({ page, baseURL }, testInfo) => {
      const name = process.env[user];
      const secret = process.env[password];
      test.skip(!name || !secret, `${user} and ${password} name the ${role}`);
      const portal = process.env.PORTAL_URL ?? baseURL ?? `https://portal.${DOMAIN}`;
      await page.goto(portal);
      await signIn(page, name as string, secret as string, portal);
      const rows: Row[] = [];
      const open = await walk(page, role, ROUTES.map((route) => new URL(route, portal).toString()), false, rows);
      // One item of each list the role could open, and the edit form of each item with one.
      const details = DETAILS.flatMap(([list, pattern]) => {
        const links = rows.find((row) => new URL(row.route).pathname === list)?.links ?? [];
        const found = links.find((href) => pattern.test(href.split(/[?#]/)[0]));
        return found ? [new URL(found.split(/[?#]/)[0], portal).toString()] : [];
      });
      const edits = details.filter((detail) => FORMS.some((section) => new URL(detail).pathname.startsWith(`${P}/${section}/`)))
        .map((detail) => `${detail}/edit`);
      await walk(open, role, [...details, ...edits], false, rows);
      report(testInfo, role, rows);
    });
  }

  test('anonymous: the public pages and the login theme read finished', async ({ page }, testInfo) => {
    const rows: Row[] = [];
    // A Portal project route without a session lands on the realm's login page.
    await walk(page, 'anonymous', [`https://portal.${DOMAIN}${P}/spaces`, ...PUBLIC_PAGES], true, rows);
    report(testInfo, 'anonymous', rows);
  });
});

import type { Browser, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 39 (T-4848): the agent screens audit. Today an administrator meets AI agents on seven
 * screens in three places: agent profiles, service accounts and MCP servers under
 * Administration, the same three per project, the assistant's agent access and the App runs. Each
 * role walks each screen at 1440 and 400 px; the journey photographs it, runs axe and writes
 * what the screen offers for the four questions of the critique (which agents exist, what may
 * each do, what did they do, how do I stop one) to `agents-ui/audit-<role>-<viewport>.json`. The critique in
 * supervisor/reports/agents-ui-<date>/ is read off these shots; T-4849, T-4850 and T-4851 re-run
 * this journey and compare.
 *
 * Asserted: every screen renders under the Portal's shell with a page heading, never a missing
 * page, and Administration refuses the viewer. Axe findings and raw permission sentences are
 * recorded, not asserted: they are the audit's input, ranked in critique.md.
 *
 * Nothing is written: every request here is a read.
 */
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';
const shotDir = path.join(reportDir, 'agents-ui');
const project = process.env.PORTAL_PROJECT || 'helsinki';

interface Screen {
  id: string;
  path: string;
  /** Administration: the viewer meets the refusal sentence there. */
  admin: boolean;
}

const SCREENS: Screen[] = [
  { id: 'org-agent-profiles', path: '/organization/agentprofiles', admin: true },
  { id: 'org-service-accounts', path: '/organization/service-accounts', admin: true },
  { id: 'org-mcp-servers', path: '/organization/mcp-servers', admin: true },
  { id: 'project-assistant-agent-access', path: `/projects/${project}/assistant`, admin: false },
  { id: 'project-service-accounts', path: `/projects/${project}/settings/service-accounts`, admin: false },
  { id: 'project-mcp-servers', path: `/projects/${project}/mcp`, admin: false },
  { id: 'connectors', path: '/connectors', admin: false },
  { id: 'project-apps-runs', path: `/projects/${project}/apps`, admin: false },
];

const ROLES = [
  { id: 'admin', user: 'PORTAL_ADMIN_USER', pass: 'PORTAL_ADMIN_PASSWORD' },
  { id: 'steward', user: 'PORTAL_USER', pass: 'PORTAL_PASSWORD' },
  { id: 'viewer', user: 'PORTAL_VIEWER_USER', pass: 'PORTAL_VIEWER_PASSWORD' },
] as const;

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 400, height: 860 },
] as const;

/** Controls that would answer "how do I stop one", by their accessible name. */
const STOP = /\b(delete|remove|revoke|disable|suspend|stop|pause|deactivate|cancel)\b/i;

interface Observation {
  role: string;
  viewport: string;
  screen: string;
  url: string;
  shot: string;
  heading: string;
  refused: boolean;
  empty: boolean;
  rows: number;
  stopControls: string[];
  scrollHeight: number;
  /** Raw "your role does not permit" sentences shown on screen, not only to a screen reader. */
  rawPermissionSentences: number;
  horizontalOverflow: boolean;
  axe: { id: string; impact: string | null | undefined; nodes: number; help: string }[];
}

async function signedIn(browser: Browser, baseURL: string | undefined, user: string, pass: string, viewport: (typeof VIEWPORTS)[number]) {
  const context = await browser.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
    locale: 'en-GB',
    viewport: { width: viewport.width, height: viewport.height },
  });
  const page = await context.newPage();
  await page.goto('/');
  await signIn(page, user, pass, baseURL);
  return { context, page };
}

async function observe(page: Page, role: string, viewport: string, screen: Screen): Promise<Observation> {
  await page.goto(screen.path);
  await page.waitForLoadState('networkidle');
  const heading = page.getByRole('heading', { level: 1 }).first();
  await expect(heading, `${screen.id}: a page heading`).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText('This page does not exist'), `${screen.id}: no missing page`).toHaveCount(0);

  const shot = `${role}-${viewport}-${screen.id}.png`;
  await page.screenshot({ path: path.join(shotDir, shot), fullPage: true });

  const main = page.getByRole('main');
  const names = await main.getByRole('button').evaluateAll((buttons) =>
    buttons.map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim()).filter(Boolean),
  );
  const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const metrics = await page.evaluate(() => {
    const root = document.scrollingElement ?? document.documentElement;
    // A disabled control's reason sits in an aria-describedby element, read by a screen reader
    // only (UI-44); a raw sentence a sighted person sees is a finding.
    const raw = Array.from(document.querySelectorAll('body *')).filter((el) => {
      const own = Array.from(el.childNodes).some((n) => n.nodeType === Node.TEXT_NODE && /does not permit '/.test(n.textContent ?? ''));
      const box = el.getBoundingClientRect();
      return own && el.checkVisibility() && box.width > 2 && box.height > 2;
    }).length;
    return { scrollHeight: root.scrollHeight, overflow: root.scrollWidth > root.clientWidth + 1, raw };
  });
  return {
    role,
    viewport,
    screen: screen.id,
    url: new URL(page.url()).pathname,
    shot,
    heading: (await heading.textContent())?.trim() ?? '',
    refused: (await page.getByText(/This page is for organization administrators/).count()) > 0,
    empty: (await main.getByText(/^No |nothing (here|yet)|none yet/i).count()) > 0,
    rows: await main.getByRole('row').count(),
    stopControls: [...new Set(names.filter((name) => STOP.test(name)))],
    scrollHeight: metrics.scrollHeight,
    rawPermissionSentences: metrics.raw,
    horizontalOverflow: metrics.overflow,
    axe: axe.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help })),
  };
}

test.describe('Journey 39: agent screens audit (T-4848)', () => {
  test.beforeAll(() => {
    fs.mkdirSync(shotDir, { recursive: true });
  });

  for (const role of ROLES) {
    for (const viewport of VIEWPORTS) {
      test(`${role.id} walks the agent screens at ${viewport.width} px`, async ({ browser, baseURL }) => {
        const user = process.env[role.user];
        const pass = process.env[role.pass];
        test.skip(!user || !pass, `${role.user} and ${role.pass} name the ${role.id}`);
        test.setTimeout(300_000);
        const { context, page } = await signedIn(browser, baseURL, user as string, pass as string, viewport);
        const seen: Observation[] = [];
        try {
          for (const screen of SCREENS) {
            const observation = await observe(page, role.id, viewport.name, screen);
            if (role.id === 'viewer' && screen.admin) {
              expect(observation.refused, `${screen.id}: Administration refuses the viewer`).toBe(true);
            }
            if (role.id === 'admin') {
              expect(observation.refused, `${screen.id}: the administrator is not refused`).toBe(false);
            }
            seen.push(observation);
          }
        } finally {
          fs.writeFileSync(path.join(shotDir, `audit-${role.id}-${viewport.name}.json`), JSON.stringify(seen, null, 2));
          await context.close();
        }
      });
    }
  }
});

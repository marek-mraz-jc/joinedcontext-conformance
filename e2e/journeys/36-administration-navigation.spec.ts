import type { Browser, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 36 (T-4831): Administration's side navigation. An organization administrator reaches
 * every section from the sidebar at 1440 and at 400 px, in its four groups, and meets no missing
 * page, no raw permission sentence and an endpoints page under three screens with a filter. A
 * viewer meets no Administration entry and no section link, and the API refuses the
 * administration routes. Every section is photographed for both people at both widths.
 *
 * Nothing is written: every request here is a read.
 */
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';

const GROUPS: Record<string, string[]> = {
  'People and access': ['People', 'Members', 'Roles', 'Groups', 'Service accounts'],
  'Projects and apps': [
    'Projects',
    'All applications',
    'All endpoints',
    'Organization MCP servers',
    'Organization models',
    'Blueprints',
    'Agent profiles',
  ],
  Platform: ['Brokers', 'Environments', 'Data space', 'Health'],
  Settings: ['Settings', 'Setup'],
};

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 400, height: 860 },
] as const;

/** The administration routes a viewer is refused with 403 (tests/authz_matrix.yaml, rule org-admin). */
const ADMIN_ROUTES = ['/api/v1/organization/health', '/api/v1/organization/brokers'];

async function shot(page: Page, role: string, viewport: string, section: string): Promise<void> {
  const dir = path.join(reportDir, 'journey-36');
  fs.mkdirSync(dir, { recursive: true });
  const name = section.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  await page.screenshot({ path: path.join(dir, `${role}-${viewport}-${name}.png`), fullPage: true });
}

/** The sidebar, opened from the menu button where it is a drawer (phone). */
async function sidebar(page: Page) {
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  if (!(await nav.isVisible())) {
    await page.getByRole('button', { name: 'Menu' }).click();
  }
  await expect(nav).toBeVisible();
  return nav;
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

test.describe('Journey 36: Administration side navigation (T-4831)', () => {
  for (const viewport of VIEWPORTS) {
    test(`an administrator reaches every section from the sidebar at ${viewport.width} px`, async ({ browser, baseURL }) => {
      const user = process.env.PORTAL_USER;
      const pass = process.env.PORTAL_PASSWORD;
      test.skip(!user || !pass, 'PORTAL_USER and PORTAL_PASSWORD name the organization administrator');
      test.setTimeout(240_000);
      const { context, page } = await signedIn(browser, baseURL, user as string, pass as string, viewport);
      try {
        await page.goto('/organization/settings');
        let nav = await sidebar(page);
        await expect(nav.getByRole('button', { name: /projects/i }), 'no project switcher in Administration').toHaveCount(0);
        for (const [group, sections] of Object.entries(GROUPS)) {
          const list = nav.getByRole('list', { name: group });
          await expect(list.getByRole('link')).toHaveText(sections);
          for (const section of sections) {
            nav = await sidebar(page);
            await nav.getByRole('list', { name: group }).getByRole('link', { name: section, exact: true }).click();
            await expect(page.getByRole('heading', { level: 1, name: 'Administration' })).toBeVisible();
            await page.waitForLoadState('networkidle');
            await expect(page.getByText('This page does not exist'), `${section}: no missing page`).toHaveCount(0);
            await expect(page.getByText(/does not permit/), `${section}: no raw permission sentence`).toHaveCount(0);
            if (section === 'All endpoints') {
              await expect(page.getByRole('searchbox', { name: /find an endpoint/i })).toBeVisible();
              if (viewport.name === 'desktop') {
                const height = await page.evaluate(() => document.scrollingElement?.scrollHeight ?? 0);
                expect(height, 'the endpoints page stays under three screens').toBeLessThan(3 * viewport.height);
              }
            }
            await shot(page, 'admin', viewport.name, section);
          }
        }
      } finally {
        await context.close();
      }
    });

    test(`a viewer meets no Administration at ${viewport.width} px, and the API refuses its routes`, async ({ browser, baseURL }) => {
      const user = process.env.PORTAL_VIEWER_USER;
      const pass = process.env.PORTAL_VIEWER_PASSWORD;
      test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');
      const { context, page } = await signedIn(browser, baseURL, user as string, pass as string, viewport);
      try {
        await expect(page.getByRole('banner').getByRole('link', { name: 'Administration' })).toHaveCount(0);
        for (const address of ['/organization/members', '/organization/settings']) {
          await page.goto(address);
          await expect(page.getByText(/This page is for organization administrators/)).toBeVisible();
        }
        const nav = await sidebar(page);
        for (const section of Object.values(GROUPS).flat()) {
          await expect(nav.getByRole('link', { name: section, exact: true })).toHaveCount(0);
        }
        await shot(page, 'viewer', viewport.name, 'administration');
        for (const route of ADMIN_ROUTES) {
          const response = await context.request.get(route);
          expect(response.status(), `${route} refuses a viewer`).toBe(403);
        }
      } finally {
        await context.close();
      }
    });
  }
});

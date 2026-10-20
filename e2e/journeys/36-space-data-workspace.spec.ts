import { test, expect, signIn, requireEnv } from '../fixtures/portal.js';
import type { Page, TestInfo } from '@playwright/test';

/**
 * Journey 36 (T-4825): a space's "Look inside" is a data workspace in Baserow's shape. It lands on
 * the rows, with the entity types and their saved views in a left list and the model, quality,
 * retention, endpoints and policies as tabs; the spaces list shows the broker's real counts (the
 * usage route answered 503 for every space on dev); the read-only viewer sees the first row in
 * under 3 s. Read-only on the demo project: nothing is proposed or written.
 */
const PROJECT = process.env.PORTAL_WORKSPACE_PROJECT || 'helsinki';
const SPACES = (process.env.PORTAL_WORKSPACE_SPACES || 'helsinki,helsinki-kpi').split(',');
/** The viewer's first row, from the address typed to the row drawn (T-4825 Done when). */
const FIRST_PAINT_MS = 3_000;

test.setTimeout(120_000);

async function shoot(page: Page, info: TestInfo, name: string): Promise<void> {
  for (const [label, width, height] of [['desktop-1440', 1440, 900], ['phone-400', 400, 860]] as const) {
    await page.setViewportSize({ width, height });
    await info.attach(`${name}-${label}`, { body: await page.screenshot(), contentType: 'image/png' });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

test.describe('space data workspace (T-4825)', () => {
  test('the spaces list shows the broker count of each demo space', async ({ steward }, info) => {
    const { page } = steward;
    await page.goto(`/projects/${PROJECT}/spaces?lang=en`);
    for (const space of SPACES) {
      const row = page.getByRole('row').filter({ has: page.getByText(space, { exact: true }) });
      await expect(row.getByTestId('space-entities')).toHaveText(/^\d[\d,. ]*$/, { timeout: 30_000 });
      await expect(row).not.toContainText('The count is not available');
    }
    await shoot(page, info, 'spaces-list');
  });

  test('Look inside lands on the rows, the types on the left, the rest as tabs', async ({ steward }, info) => {
    const { page } = steward;
    await page.goto(`/projects/${PROJECT}/spaces/${SPACES[0]}?lang=en`);
    const tabs = page.getByRole('tablist', { name: 'Parts of the space' });
    await expect(tabs.getByRole('tab', { name: 'Data' })).toHaveAttribute('aria-selected', 'true');
    for (const name of ['Data model', 'Quality', 'Retention', 'Endpoints', 'Policies']) {
      await expect(tabs.getByRole('tab', { name: new RegExp(name, 'i') })).toBeVisible();
    }
    const types = page.getByRole('navigation', { name: 'Entity types' });
    await expect(types.locator('[aria-current="true"]').first()).toBeVisible();
    await expect(types.getByRole('list', { name: 'Saved views' })).toBeVisible();
    await expect(page.getByRole('tabpanel').getByRole('row').nth(1)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('space-total')).toContainText(/holds [\d,. ]+ entit/, { timeout: 30_000 });
    // The page is the workspace, no longer the model diagram followed by the grid (14,515 px).
    const height = await page.evaluate(() => document.scrollingElement?.scrollHeight ?? 0);
    expect(height).toBeLessThan(4_000);
    await shoot(page, info, 'look-inside');

    await tabs.getByRole('tab', { name: /model/i }).click();
    await expect(page).toHaveURL(/[?&]tab=model/);
    await expect(page.getByRole('table', { name: 'Entity types' })).toBeVisible();
  });

  test('the viewer sees the first row in under 3 s', async ({ browser, baseURL }, info) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');
    const context = await browser.newContext({ baseURL: baseURL ?? requireEnv('PORTAL_URL'), ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string, baseURL);
    // The slowest call is named in the report: every API answer of the page with its time.
    const timings: string[] = [];
    page.on('requestfinished', (request) => {
      const timing = request.timing();
      const url = new URL(request.url());
      if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/cs/')) {
        timings.push(`${Math.round(timing.responseEnd)} ms ${url.pathname}`);
      }
    });
    const start = Date.now();
    await page.goto(`/projects/${PROJECT}/spaces/${SPACES[0]}?lang=en`);
    await expect(page.getByRole('tabpanel').getByRole('row').nth(1)).toBeVisible({ timeout: 30_000 });
    const elapsed = Date.now() - start;
    await info.attach('viewer-first-paint', { body: [`first row ${elapsed} ms`, ...timings.sort((a, b) => parseInt(b) - parseInt(a))].join('\n'), contentType: 'text/plain' });
    await shoot(page, info, 'viewer-look-inside');
    await context.close();
    expect(elapsed).toBeLessThan(FIRST_PAINT_MS);
  });
});

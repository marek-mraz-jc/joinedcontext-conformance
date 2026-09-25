import { writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * The page budgets of T-2800, measured nightly on dev: each main page of a project, opened cold
 * in a fresh browser context (no cache) as the read-only demo viewer. It records the largest
 * contentful paint, the layout shift and the JavaScript the page transferred, and writes them to
 * `$JC_BUDGETS_OUT`; `scripts/budgets-dev.py` holds the budgets and decides. The test itself
 * only fails when a page does not render.
 *
 * Runs only when JC_BUDGETS_OUT is set, so the regular journey run skips it.
 */
const PAGES = ['spaces', 'endpoints', 'models', 'explore', 'apps', 'assistant'];

type Measure = { page: string; lcpMs: number | null; cls: number; jsBytes: number; loadMs: number };

test.describe('Page budgets (T-2800)', () => {
  test.skip(!process.env.JC_BUDGETS_OUT, 'JC_BUDGETS_OUT names the file the measures go to');

  test('every main page renders, and its timings are recorded', async ({ browser, baseURL }) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    const project = process.env.PORTAL_PROJECT || 'helsinki';
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');
    test.setTimeout(PAGES.length * 60_000);

    const login = await browser.newContext();
    const first = await login.newPage();
    await first.goto('/');
    await signIn(first, user as string, pass as string, baseURL);
    const state = await login.storageState();
    await login.close();

    const measures: Measure[] = [];
    for (const name of PAGES) {
      const context = await browser.newContext({ storageState: state });
      const page = await context.newPage();
      const started = Date.now();
      await page.goto(`/projects/${project}/${name}`, { waitUntil: 'load' });
      await expect(page.getByRole('heading').first(), `${name} renders a heading`).toBeVisible({ timeout: 20_000 });
      await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => undefined);
      const loadMs = Date.now() - started;
      const timings = await page.evaluate(async () => {
        const buffered = (type: string) =>
          new Promise<PerformanceEntry[]>((resolve) => {
            const entries: PerformanceEntry[] = [];
            const observer = new PerformanceObserver((list) => entries.push(...list.getEntries()));
            observer.observe({ type, buffered: true });
            setTimeout(() => { observer.disconnect(); resolve(entries); }, 500);
          });
        const lcp = await buffered('largest-contentful-paint');
        const shifts = (await buffered('layout-shift')) as (PerformanceEntry & { value: number; hadRecentInput: boolean })[];
        const scripts = (performance.getEntriesByType('resource') as PerformanceResourceTiming[])
          .filter((entry) => entry.initiatorType === 'script' || /\.m?js(\?|$)/.test(entry.name));
        return {
          lcpMs: lcp.length ? lcp[lcp.length - 1].startTime : null,
          cls: shifts.filter((s) => !s.hadRecentInput).reduce((sum, s) => sum + s.value, 0),
          jsBytes: scripts.reduce((sum, entry) => sum + entry.transferSize, 0),
        };
      });
      measures.push({ page: name, loadMs, ...timings });
      await context.close();
    }

    const out = process.env.JC_BUDGETS_OUT as string;
    mkdirSync(path.dirname(out), { recursive: true });
    writeFileSync(out, JSON.stringify({ pages: measures }, null, 2));
  });
});

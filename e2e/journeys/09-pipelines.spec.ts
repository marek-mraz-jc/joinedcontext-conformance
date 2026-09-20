import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 9 (T-0354): DEMO.md step 3 — the Pipelines view shows the ingest running and its
 * counter moves while the demo is on screen. TS-12 (clicked, not typed), PL-01, PL-08,
 * PL-24 (the numbers come from the runner), UI-25, PL-17 (a credential is named, never printed).
 *
 * Everything is read from the table the user sees, not from the metrics route: a view that shows
 * a stale or empty number while the route is healthy is exactly the failure this journey exists
 * for, and it is the one the deployment gap (no runner URL) produces.
 */
const pipelines = /pipelines|kanály|toky/i;
const PIPELINE = process.env.PORTAL_PIPELINE || 'aq-mqtt-ingest';

/** The first integer in a row, which is the received counter the view renders. */
function counterOf(text: string): number | undefined {
  const match = text.replace(/ /g, ' ').match(/(\d[\d\s,.]*)\s*(received|prijat)/i)
    ?? text.match(/\b(\d[\d\s,.]*)\b/);
  if (!match) return undefined;
  const digits = match[1].replace(/[^\d]/g, '');
  return digits ? Number(digits) : undefined;
}

test.describe('Journey 9: a running pipeline and a counter that moves (T-0354)', () => {
  test('PL-24 the pipeline is listed live and its counter grows', async ({ steward }) => {
    const { page } = steward;
    await openView(page, pipelines);

    const row = page.getByRole('row').filter({ hasText: PIPELINE }).first();
    await expect(
      row,
      `PL-01: ${PIPELINE} must be listed in the Pipelines view — a view with no pipeline is a failure, not a skip`,
    ).toBeVisible({ timeout: 30_000 });

    await expect(
      row.getByText(/^\s*(live|running|beží|nasadené)\s*$/i).first(),
      'PL-08: the pipeline must report that it is running',
    ).toBeVisible({ timeout: 60_000 });

    const first = counterOf(await row.innerText());
    expect(
      first,
      'PL-24: the view must show the runner counters; "no numbers" means the runner URL is unset in the deployment',
    ).toBeGreaterThanOrEqual(0);

    // The view refreshes itself; the journey waits for its refresh rather than reloading, so a
    // view that only updates on a full page load fails here.
    await expect
      .poll(
        async () => counterOf(await row.innerText()) ?? -1,
        {
          message: 'PL-24: the received counter must grow while the ingest runs',
          timeout: 120_000,
          intervals: [5_000],
        },
      )
      .toBeGreaterThan(first as number);
  });

  test('PL-17 the view names the credential by reference and never prints its value', async ({ steward }) => {
    const { page } = steward;
    await openView(page, pipelines);

    const row = page.getByRole('row').filter({ hasText: PIPELINE }).first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    await row.click();

    const body = await page.locator('body').innerText();
    expect(
      body,
      'PL-17: a pipeline view shows the secret reference, never a secret value',
    ).not.toMatch(/-----BEGIN|password\s*[:=]\s*\S|client[_-]?secret\s*[:=]\s*\S/i);
  });
});

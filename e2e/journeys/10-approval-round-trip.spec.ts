import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 10 (T-0355): DEMO.md step 7 end to end in the browser — a change made in a form becomes
 * a merge request, an approval, a commit and a live representation. TS-12, UI-23, UI-24, CC-20,
 * CC-34.
 *
 * The endpoint of journey 7 gains the `csv` representation; the run puts it back afterwards, so
 * the demo cluster is in its pre-test shape whether the journey passes or fails.
 */
const endpoints = /endpoints|koncové body/i;
const approvals = /approvals|schválenia/i;
const NAME = 'public-air';

async function slugOf(page: import('@playwright/test').Page): Promise<string> {
  await openView(page, endpoints);
  const row = page.getByRole('row').filter({ hasText: NAME }).first();
  await expect(row, `journey 7 must have created ${NAME} first`).toBeVisible({ timeout: 60_000 });
  const slug = (await row.innerText()).match(/\b[a-z2-7]{26}\b/)?.[0];
  expect(slug, 'the endpoint row must show a slug').toBeTruthy();
  return slug as string;
}

/** Ticks or unticks one representation in the endpoint form and proposes the change. */
async function setRepresentation(
  page: import('@playwright/test').Page,
  representation: RegExp,
  enabled: boolean,
): Promise<void> {
  await openView(page, endpoints);
  const row = page.getByRole('row').filter({ hasText: NAME }).first();
  await row.getByRole('button', { name: /edit|upraviť/i }).or(row.getByRole('link', { name: /edit|upraviť/i })).first().click();

  const form = page.getByRole('form').or(page.locator('form')).first();
  await expect(form, 'UI-04: the endpoint form must open').toBeVisible({ timeout: 20_000 });
  const box = form.getByRole('checkbox', { name: representation });
  if (enabled) await box.check();
  else await box.uncheck();

  await page.getByRole('button', { name: /propose change|navrhnúť zmenu/i }).first().click();
}

/** Approves the newest open proposal and waits for it to reach live. */
async function approveNewest(page: import('@playwright/test').Page): Promise<void> {
  await openView(page, approvals);
  const pending = page.getByRole('row').or(page.getByRole('article'))
    .filter({ hasText: /pending|čaká/i })
    .first();
  await expect(pending, 'UI-23: the proposal must be listed for approval').toBeVisible({ timeout: 60_000 });
  await pending.getByRole('button', { name: /review|open|detail|zobraziť/i })
    .or(pending.getByRole('link', { name: /review|open|detail|zobraziť/i }))
    .first()
    .click();
  await page.getByRole('button', { name: /^approve|schváliť/i }).first().click();
  await expect(
    page.getByText(/^\s*(live|nasadené)\s*$/i).first(),
    'CC-33: an approved change must reach live on its own',
  ).toBeVisible({ timeout: 180_000 });
}

test.describe('Journey 10: form change → merge request → approval → live representation (T-0355)', () => {
  test.afterEach(async ({ approver }) => {
    // The demo cluster is left as it was found, whatever happened above.
    await setRepresentation(approver.page, /csv/i, false).catch(() => undefined);
    await approveNewest(approver.page).catch(() => undefined);
  });

  test('CC-20 the change travels from the form to a served CSV', async ({ steward, approver, request, baseURL }) => {
    const slug = await slugOf(steward.page);

    await setRepresentation(steward.page, /csv/i, true);
    await expect(
      steward.page.getByText(/proposal|pending|návrh|čaká/i).first(),
      'UI-23: a form save is a proposal, never a direct write to the live endpoint',
    ).toBeVisible({ timeout: 30_000 });

    // UI-24: the approver reads a sentence and a diff, not a manifest.
    await openView(approver.page, approvals);
    const pending = approver.page.getByRole('row').or(approver.page.getByRole('article'))
      .filter({ hasText: /pending|čaká/i })
      .first();
    await expect(pending, 'UI-23: the proposal must reach the approvals view').toBeVisible({ timeout: 60_000 });
    await pending.getByRole('button', { name: /review|open|detail|zobraziť/i })
      .or(pending.getByRole('link', { name: /review|open|detail|zobraziť/i }))
      .first()
      .click();
    await expect(
      approver.page.getByRole('heading', { name: /summary|zhrnutie/i }).first(),
      'UI-24: the change must be summarized in plain language',
    ).toBeVisible({ timeout: 20_000 });
    await expect(
      approver.page.getByText(/csv/i).first(),
      'UI-24: the plan diff must name what the change adds',
    ).toBeVisible({ timeout: 20_000 });

    await approver.page.getByRole('button', { name: /^approve|schváliť/i }).first().click();

    // CC-20: the representation is live only after the reconciler applied the merged commit.
    await expect
      .poll(
        async () => {
          const response = await request.get(`${baseURL}/api/endpoint/${slug}/file.csv`, {
            failOnStatusCode: false,
          });
          return `${response.status()} ${response.headers()['content-type'] ?? ''}`;
        },
        {
          message: 'CC-20: an approved change must make file.csv answer CSV',
          timeout: 240_000,
          intervals: [5_000],
        },
      )
      .toMatch(/^200 .*text\/csv/);
  });

  test('CC-34 the read-only viewer is offered no way to approve', async ({ browser, baseURL }) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');

    const { signIn } = await import('../fixtures/portal.js');
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string);
    await openView(page, approvals);

    const approve = page.getByRole('button', { name: /^approve|schváliť/i });
    const count = await approve.count();
    for (let i = 0; i < count; i++) {
      await expect(
        approve.nth(i),
        'CC-34: a viewer without the approver role must never be able to approve',
      ).toBeDisabled();
    }
    await context.close();
  });
});

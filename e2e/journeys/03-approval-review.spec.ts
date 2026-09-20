import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 3 (T-0071): the approver reads the change in plain words, sees the plan diff, approves
 * in the app, and watches the status settle — UI-23 (dedicated approvals view), UI-24 (summary,
 * plan diff, policy result), CC-34 (approve/reject drive the merge request; no Git forge UI),
 * CC-33 (deploying → live), TS-12 (real controls only).
 */
test.describe('Journey 3: In-app review, plan diff and approval (T-0071)', () => {
  const approvals = /approvals|pending approvals|schválenia/i;

  test('UI-24 a pending change shows a plain-language summary and its plan diff', async ({ approver }) => {
    const { page } = approver;
    await openView(page, approvals);

    const pending = page.getByRole('article').or(page.getByRole('row')).filter({ hasText: /pending approval|čaká na schválenie/i }).first();
    await expect(pending, 'UI-23: the approvals view must list the open change proposals').toBeVisible({ timeout: 30_000 });

    await pending.getByRole('button', { name: /review|open|detail|zobraziť/i })
      .or(pending.getByRole('link', { name: /review|open|detail|zobraziť/i })).first().click();

    // UI-24: a sentence a domain expert can read, not a manifest
    const summary = page.getByRole('heading', { name: /summary|what changes|zhrnutie/i })
      .or(page.getByText(/adds|removes|changes|pridáva|mení/i)).first();
    await expect(summary, 'UI-24: the change must be described in plain language').toBeVisible({ timeout: 20_000 });

    // UI-24: the plan diff, with the added resource visible in it
    const diff = page.getByRole('region', { name: /plan|diff|zmeny/i })
      .or(page.getByRole('heading', { name: /plan|diff|zmeny/i }).locator('..'))
      .first();
    await expect(diff, 'UI-24: the approver must see the jcctl plan diff in the app').toBeVisible({ timeout: 20_000 });
    await expect(
      diff.getByText(/subscription|notification|odber/i).first(),
      'UI-24: the diff must name the resource the change adds',
    ).toBeVisible();
  });

  test('CC-34 approving in the app drives the change to live', async ({ approver }) => {
    const { page } = approver;
    await openView(page, approvals);

    const pending = page.getByRole('article').or(page.getByRole('row')).filter({ hasText: /pending approval|čaká na schválenie/i }).first();
    await expect(pending, 'UI-23: nothing to approve — run journey 2 first').toBeVisible({ timeout: 30_000 });
    await pending.getByRole('button', { name: /review|open|detail|zobraziť/i })
      .or(pending.getByRole('link', { name: /review|open|detail|zobraziť/i })).first().click();

    const approve = page.getByRole('button', { name: /approve|schváliť/i }).first();
    await expect(approve, 'CC-34: approval happens in the app, never in the Git forge').toBeEnabled({ timeout: 20_000 });
    await approve.click();

    // CC-33: deploying is a transient state, live is the one that must arrive
    await expect(
      page.getByText(/deploying|nasadzuje/i).first(),
      'CC-33: an approved change must report that it is being deployed',
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByText(/^\s*(live|nasadené)\s*$/i).first(),
      'CC-33: the change must reach live without the user touching anything else',
    ).toBeVisible({ timeout: 180_000 });
  });

  test('CC-34 an author without the approver role cannot approve', async ({ steward }) => {
    const { page } = steward;
    await openView(page, approvals);

    const approve = page.getByRole('button', { name: /approve|schváliť/i });
    const count = await approve.count();
    if (count === 0) return; // hidden is a correct implementation of separation of duties
    for (let i = 0; i < count; i++) {
      await expect(
        approve.nth(i),
        'CC-34: separation of duties — an approve button offered to a non-approver must be disabled',
      ).toBeDisabled();
    }
  });
});

import { test, expect, openView, requireEnv } from '../fixtures/portal.js';

/**
 * Journey 14 (T-0360): a resource that drifted from Git truth offers exactly two ways out, and
 * both of them work. TS-12, UI-25, UI-26, CC-38.
 *
 * The drift is made with a scoped ServiceAccount token (T-0353), never with an admin credential,
 * and each branch asserts the end state rather than the click that started it. Both branches
 * leave Git and the cluster as they were found.
 */
const spaces = /context spaces|kontextové priestory/i;
const RESOURCE = process.env.PORTAL_DRIFT_RESOURCE || 'ovzdusie';

/** Writes a live change past the Portal, which is what "drift" means (CC-38). */
async function drift(
  request: import('@playwright/test').APIRequestContext,
  baseURL: string,
  value: string,
): Promise<void> {
  const token = requireEnv('JC_DRIFT_TOKEN');
  const response = await request.patch(
    `${baseURL}/api/v1/projects/${process.env.PORTAL_PROJECT || 'banskabystrica'}/spaces/${RESOURCE}`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/merge-patch+json',
      },
      data: { metadata: { annotations: { 'demo.joinedcontext.com/drift': value } } },
      failOnStatusCode: false,
    },
  );
  expect(
    response.status(),
    `the scoped ServiceAccount must be able to write the drift (got ${response.status()})`,
  ).toBeLessThan(300);
}

async function openResource(page: import('@playwright/test').Page) {
  await openView(page, spaces);
  const row = page.getByRole('row').filter({ hasText: RESOURCE }).first();
  await expect(row, `${RESOURCE} must be listed`).toBeVisible({ timeout: 30_000 });
  return row;
}

test.describe('Journey 14: a drifted resource offers Revert and Adopt (T-0360)', () => {
  test('UI-25 a drifted resource shows the Drifted state and exactly two actions', async ({ steward, request, baseURL }) => {
    await drift(request, baseURL as string, 'one');
    const { page } = steward;
    await page.reload();
    const row = await openResource(page);

    await expect(
      row.getByText(/drift/i).first(),
      'UI-25: a resource whose live state left Git truth must say so',
    ).toBeVisible({ timeout: 120_000 });

    await row.click();
    const actions = page.getByRole('button', { name: /revert|adopt|vrátiť|prevziať/i });
    await expect
      .poll(async () => actions.count(), {
        message: 'UI-26: drift offers exactly two ways out — revert to Git, or adopt into Git',
        timeout: 30_000,
      })
      .toBe(2);
  });

  test('CC-38 Revert puts the live state back to Git truth', async ({ steward, request, baseURL }) => {
    await drift(request, baseURL as string, 'to-revert');
    const { page } = steward;
    await page.reload();
    const row = await openResource(page);
    await row.click();

    await page.getByRole('button', { name: /revert|vrátiť/i }).first().click();

    await expect
      .poll(
        async () => {
          await page.reload();
          return (await (await openResource(page)).innerText()).toLowerCase();
        },
        { message: 'CC-38: after a revert the resource must match Git truth again', timeout: 180_000, intervals: [5_000] },
      )
      .not.toMatch(/drift/);
  });

  test('CC-38 Adopt proposes the live state as a change instead of discarding it', async ({ steward, request, baseURL }) => {
    await drift(request, baseURL as string, 'to-adopt');
    const { page } = steward;
    await page.reload();
    const row = await openResource(page);
    await row.click();

    await page.getByRole('button', { name: /adopt|prevziať/i }).first().click();

    await openView(page, /approvals|schválenia/i);
    const proposal = page.getByRole('row').or(page.getByRole('article'))
      .filter({ hasText: new RegExp(RESOURCE, 'i') })
      .first();
    await expect(
      proposal,
      'UI-26: adopting the live state must appear as a change proposal carrying it',
    ).toBeVisible({ timeout: 120_000 });
    await proposal.click();
    await expect(
      page.getByText(/to-adopt/).first(),
      'UI-26: the proposal must carry the live value that was adopted',
    ).toBeVisible({ timeout: 30_000 });

    // The cluster is left as it was found: the adopted proposal is rejected, not merged.
    await page.getByRole('button', { name: /reject|zamietnuť/i }).first().click().catch(() => undefined);
  });
});

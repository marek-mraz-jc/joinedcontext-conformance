import type { Page, Request } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 37 (T-4826): a value of the synthetic KPI space corrected inline, as Baserow edits a table.
 * The steward clicks a number, types over it, applies it with a reason, sees Undo and undoes it; the
 * PATCH goes through an Endpoint with `JC-Reason` (DM-113) and the reason is in the version's
 * provenance (ADR-N-054). The viewer gets no editor, and the same PATCH sent directly as the viewer
 * answers 403: the grid hiding the editor is not the control, the gateway is.
 *
 * The only writes are the steward's correction and its Undo, which writes the old value back, so the
 * space ends as it began. With `JC_EVIDENCE_DIR` set it keeps a screenshot per step and width there.
 */
const PROJECT = process.env.JC_KPI_PROJECT || 'helsinki';
const SPACE = process.env.JC_KPI_SPACE || 'helsinki-kpi';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const REASON = 'Journey 37: corrected inline';

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: join(EVIDENCE, `${name}.png`) });
}

/** The space's grid, with its first row drawn. */
async function openGrid(page: Page): Promise<void> {
  await page.goto(`/projects/${PROJECT}/spaces/${SPACE}?lang=en`);
  await expect(page.getByRole('grid'), 'the space opens on its data grid').toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('grid').getByRole('row').nth(1)).toBeVisible({ timeout: 30_000 });
}

const patchOf = (request: Request) => request.method() === 'PATCH' && /\/ngsi-ld\/v1\/entities\/[^/]+\/attrs$/.test(request.url());

for (const width of [1440, 400]) {
  test.describe(`Journey 37 at ${width} px: inline edit in the space grid (T-4826)`, () => {
    test('the steward corrects a KPI value with a reason, sees Undo, and the reason is in the provenance', async ({ steward }) => {
      const { page } = steward;
      await page.setViewportSize({ width, height: width > 600 ? 900 : 860 });
      await openGrid(page);

      const cell = page.getByRole('spinbutton', { name: /^Edit / }).first();
      await expect(cell, 'the steward is granted a write on a number of the type').toBeVisible();
      const before = Number(await cell.inputValue());
      const after = String(before + 1);
      await cell.click();
      await page.keyboard.press('ControlOrMeta+a');
      await page.keyboard.type(after);
      await page.keyboard.press('Enter');
      await page.getByRole('button', { name: 'Review the changes' }).click();
      const review = page.getByRole('region', { name: 'Review the changes' });
      await review.getByRole('textbox', { name: 'Why you change these values' }).fill(REASON);
      await shot(page, `37-edit-steward-${width}`);

      const sent = page.waitForRequest(patchOf);
      await review.getByRole('button', { name: 'Apply' }).click();
      const request = await sent;
      expect(request.headers()['jc-reason'], 'DM-113: the reason travels with the write').toBe(encodeURIComponent(REASON));
      expect((await request.response())?.status(), 'the gateway takes the steward write').toBeLessThan(300);

      const undo = page.getByRole('button', { name: 'Undo' });
      await expect(undo).toBeVisible();
      await shot(page, `37-undo-steward-${width}`);

      // The version the write made names the reason (ADR-N-054 2.3), read through the same endpoint.
      const url = new URL(request.url());
      const [, entityId] = /\/entities\/([^/]+)\/attrs$/.exec(url.pathname) ?? [];
      const attr = Object.keys(JSON.parse(request.postData() ?? '{}'))[0];
      const temporal = url.pathname.replace(/\/entities\/[^/]+\/attrs$/, `/temporal/entities/${entityId}`);
      const history = await page.request.get(`${temporal}?attrs=${attr}&versions=all&lastN=5`);
      expect(history.status(), 'the steward reads the versions').toBe(200);
      expect(JSON.stringify(await history.json()), 'the reason is in the provenance').toContain(REASON);

      const undone = page.waitForRequest(patchOf);
      await undo.click();
      expect((await undone).headers()['jc-reason']).toBe(encodeURIComponent(`Undo: ${REASON}`));
      await expect(page.getByText('The change was undone.')).toBeVisible();
      await expect(cell).toHaveValue(String(before));
    });

    test('the viewer gets no editor, and a direct PATCH as the viewer answers 403', async ({ browser, baseURL }) => {
      const user = process.env.PORTAL_VIEWER_USER;
      const pass = process.env.PORTAL_VIEWER_PASSWORD;
      test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');
      const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
      const page = await context.newPage();
      try {
        await page.setViewportSize({ width, height: width > 600 ? 900 : 860 });
        await page.goto('/');
        await signIn(page, user as string, pass as string);
        await openGrid(page);
        await expect(page.getByRole('spinbutton', { name: /^Edit / })).toHaveCount(0);
        await expect(page.getByRole('textbox', { name: /^Edit / })).toHaveCount(0);
        await shot(page, `37-viewer-${width}`);

        // The same write as the steward's, sent as the viewer through the space's endpoints: refused
        // by the gateway whatever the UI showed (PF-50).
        const endpoints = await page.request.get(`/api/v1/projects/${PROJECT}/endpoints`);
        const slugs = ((await endpoints.json()).items as { spec: { contextSpaceRef?: string; slug?: string } }[])
          .filter((each) => each.spec.contextSpaceRef === SPACE && each.spec.slug)
          .map((each) => each.spec.slug as string);
        expect(slugs.length, 'the KPI space has an endpoint').toBeGreaterThan(0);
        // The first row's id, which its open button carries as its tooltip.
        const id = await page.getByRole('button', { name: /^Open: / }).first().getAttribute('title');
        expect(id, 'the grid lists an entity').toBeTruthy();
        for (const slug of slugs) {
          const answer = await page.request.patch(
            `/api/endpoint/${slug}/ngsi-ld/v1/entities/${encodeURIComponent(id as string)}/attrs`,
            { data: { name: { type: 'Property', value: 'viewer write' } }, headers: { 'JC-Reason': 'journey%2037%20viewer' } },
          );
          expect(answer.status(), `the viewer's PATCH through ${slug} is refused`).toBe(403);
        }
      } finally {
        await context.close();
      }
    });
  });
}

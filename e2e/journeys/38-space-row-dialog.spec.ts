import type { Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect } from '../fixtures/portal.js';

/**
 * Journey 38 (T-4828): a row of the synthetic KPI space opened as a dialog, as Baserow expands a row.
 * The steward opens a row from the grid; the address names it (`?row=<urn>`), so a reload opens the
 * same dialog. Fields edits with the grid's own editor, History reads the attribute's temporal values
 * through the space surface and its versions with who changed them and why (DM-114), Comments posts
 * a comment and removes it again. "Add row" opens the type's form.
 *
 * Edits stay pending and are discarded, and the comment is removed, so the space ends as it began.
 * The inline editor comes with T-4826 (merge-after). With `JC_EVIDENCE_DIR` set it keeps a screenshot
 * per step and width there.
 */
const PROJECT = process.env.JC_KPI_PROJECT || 'helsinki';
const SPACE = process.env.JC_KPI_SPACE || 'helsinki-kpi';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const COMMENT = `Journey 38 comment ${Date.now()}`;

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: join(EVIDENCE, `${name}.png`) });
}

for (const width of [1440, 400]) {
  test.describe(`Journey 38 at ${width} px: a row as a dialog (T-4828)`, () => {
    test('the steward opens, edits, reads the history of and comments on a KPI row; the link reopens it', async ({ steward }) => {
      const { page } = steward;
      await page.setViewportSize({ width, height: width > 600 ? 900 : 860 });
      await page.goto(`/projects/${PROJECT}/spaces/${SPACE}?lang=en`);
      await expect(page.getByRole('grid').getByRole('row').nth(1), 'the space opens on its rows').toBeVisible({ timeout: 30_000 });

      const open = page.getByRole('button', { name: /^Open: / }).first();
      const urn = await open.getAttribute('title');
      expect(urn, 'the first row names its entity').toMatch(/^urn:ngsi-ld:/);
      await open.click();
      const dialog = page.getByRole('dialog', { name: /^Details: / });
      await expect(dialog).toBeVisible();
      expect(new URL(page.url()).searchParams.get('row'), 'the address names the open row').toBe(urn);
      await expect(dialog.getByRole('tab')).toHaveText(['Fields', 'History', 'Comments']);

      // Fields: the grid's own editor, pending until applied; discarded so nothing is written.
      const editor = dialog.getByRole('spinbutton').first();
      await expect(editor, 'the steward may edit a number of the type').toBeVisible();
      await editor.fill(String(Number(await editor.inputValue()) + 1));
      await expect(dialog.getByText(/not applied yet/)).toBeVisible();
      await shot(page, `38-fields-steward-${width}`);
      await dialog.getByRole('button', { name: 'Discard' }).click();

      // History: the temporal read of one attribute, through the space surface the grid reads.
      const temporal = page.waitForResponse((answer) => answer.url().includes('/ngsi-ld/v1/temporal/entities/'));
      await dialog.getByRole('tab', { name: 'History' }).click();
      expect((await temporal).status(), 'the steward reads the history').toBeLessThan(300);
      const versions = page.waitForResponse((answer) => answer.url().includes('versions=all'));
      await dialog.getByRole('checkbox', { name: /Every version/ }).check();
      expect((await versions).status(), 'the steward is granted readVersions').toBeLessThan(300);
      await expect(dialog.getByRole('columnheader', { name: 'Changed by' })).toBeVisible();
      await shot(page, `38-history-steward-${width}`);

      // Comments: posted and removed again.
      await dialog.getByRole('tab', { name: 'Comments' }).click();
      await dialog.getByRole('textbox', { name: 'Add a comment' }).fill(COMMENT);
      await dialog.getByRole('button', { name: 'Comment', exact: true }).click();
      await expect(dialog.getByText(COMMENT)).toBeVisible();
      await shot(page, `38-comments-steward-${width}`);
      await dialog.getByRole('listitem').filter({ hasText: COMMENT }).getByRole('button', { name: /^Remove/ }).click();
      await expect(dialog.getByText(COMMENT)).toHaveCount(0);

      // The link opens the same row again, and closing takes it out of the address.
      await page.reload();
      await expect(page.getByRole('dialog', { name: /^Details: / })).toBeVisible({ timeout: 30_000 });
      await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
      await expect(page.getByRole('dialog')).toHaveCount(0);
      expect(new URL(page.url()).searchParams.get('row')).toBeNull();

      // "Add row" opens the type's form; nothing is submitted.
      await page.getByRole('button', { name: 'Add row' }).click();
      const form = page.getByRole('dialog', { name: /^New / });
      await expect(form.getByRole('button', { name: 'Create', exact: true })).toBeVisible();
      await shot(page, `38-add-row-steward-${width}`);
    });
  });
}

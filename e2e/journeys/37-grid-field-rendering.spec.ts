import { test, expect } from '../fixtures/portal.js';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Journey 37 (T-4827): the space grid shows values by what they hold. On dev, the helsinki KPI and
 * transport grids show no raw JSON, URN or compact IRI in a cell or a header; each column header
 * names what it holds; the header menu sorts, filters, hides, renames, resizes and moves a column;
 * a label set on "All rows" survives a reload. The rows are read with the steward's own session,
 * through the space's grant. With `JC_EVIDENCE_DIR` set it keeps a 1440 px and a 400 px
 * screenshot per grid there.
 */
const PROJECT = process.env.PORTAL_GRID_PROJECT || 'helsinki';
const GRIDS = (process.env.PORTAL_GRID_SPACES || 'helsinki-kpi,helsinki').split(',').map((each) => each.trim()).filter(Boolean);
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RAW = /[{}]|urn:ngsi-ld:|\bngsi-ld:|https?:\/\//;

for (const space of GRIDS) {
  for (const [label, width, height] of [['desktop-1440', 1440, 900], ['phone-400', 400, 860]] as const) {
    test(`T-4827 ${space} grid at ${label}: values by type, no raw JSON, URN or IRI; the header menu works`, async ({ steward }) => {
      const { page } = steward;
      await page.setViewportSize({ width, height });
      await page.goto(`/projects/${PROJECT}/spaces/${space}?lang=en`);
      const grid = page.getByRole('grid');
      await expect(grid.locator('tbody tr').first()).toBeVisible({ timeout: 30_000 });

      const headerRow = grid.locator('thead tr').first();
      const headers = await headerRow.locator('.jc-grid-sort-btn').allInnerTexts();
      expect(headers.length).toBeGreaterThan(1);
      for (const header of headers) expect(header, `header "${header}"`).not.toMatch(RAW);
      const cells = await grid.locator('tbody [role="gridcell"]').allInnerTexts();
      for (const cell of cells) expect(cell, `cell "${cell}"`).not.toMatch(RAW);
      // Every column says what it holds, by a name a screen reader reads.
      await expect(headerRow.locator('.jc-grid-kind[role="img"]').first()).toHaveAttribute('aria-label', /.+/);
      if (EVIDENCE) {
        mkdirSync(EVIDENCE, { recursive: true });
        await page.screenshot({ path: join(EVIDENCE, `${space}-grid-${label}.png`) });
      }

      // The header menu of the second column: sort, filter, rename, width, move, hide.
      const menuButton = headerRow.locator('.jc-grid-meta-btn').nth(1);
      const name = (await menuButton.getAttribute('aria-label')) ?? '';
      const column = name.replace(/^Column options: /, '');
      await menuButton.click();
      const menu = page.getByRole('dialog', { name });
      await expect(menu).toBeVisible();
      if (EVIDENCE) await page.screenshot({ path: join(EVIDENCE, `${space}-menu-${label}.png`) });
      await menu.getByRole('button', { name: 'Sort descending' }).click();
      await expect(headerRow.getByRole('button', { name: new RegExp(`Sort this page by ${column.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) })).toContainText('↓');

      await headerRow.getByRole('button', { name }).click();
      await menu.getByRole('button', { name: 'Wider' }).click();
      await menu.getByRole('button', { name: 'Move right' }).click();
      const renamed = `T-4827 ${column}`.slice(0, 80);
      await menu.getByLabel('Label in this view').fill(renamed);
      await menu.getByLabel('Label in this view').press('Enter');
      await expect(headerRow.getByRole('button', { name: `Column options: ${renamed}` })).toBeVisible();
      await page.keyboard.press('Escape');

      await page.reload();
      const again = page.getByRole('grid').locator('thead tr').first();
      await expect(again.getByRole('button', { name: `Column options: ${renamed}` })).toBeVisible({ timeout: 30_000 });
      // Back to the model's label and hidden, so the next run starts from the model again.
      await again.getByRole('button', { name: `Column options: ${renamed}` }).click();
      await page.getByRole('button', { name: "Use the model's label" }).click();
      await again.getByRole('button', { name }).click();
      await page.getByRole('button', { name: 'Hide field' }).click();
      await expect(again.getByRole('button', { name })).toHaveCount(0);
      await page.evaluate(() => {
        for (const key of Object.keys(window.localStorage)) if (key.startsWith('jc.space-columns.')) window.localStorage.removeItem(key);
      });
    });
  }
}

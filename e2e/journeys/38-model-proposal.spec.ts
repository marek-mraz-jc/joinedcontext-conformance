import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/portal.js';

/**
 * Journey 38 (T-4830): a model change is proposed with its diff and the version it makes, and read
 * back. On every Helsinki model the Form tab draws each class without a schema error ("Unsupported
 * field schema", "Missing items definition", "Any option 1") and with no help text citing a task or
 * a spec page; History lists the model's closed Changes, the published one included; the editor's
 * Propose button stays closed until an edit and says why. Read-only: dev keeps nothing. With
 * `JC_EVIDENCE_DIR` set it keeps the 1440 px and 400 px screenshots there.
 */
const PROJECT = process.env.PORTAL_MODELS_PROJECT || 'helsinki';
const MODELS = (process.env.PORTAL_MODELS_HELSINKI || 'helsinki,helsinki-kpi,helsinki-hub,mobility').split(',');
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const SCHEMA_ERROR = /Unsupported field schema|Missing items definition|Any option \d/;
const INTERNAL = /\bT-\d{4}\b|\b(?:Development|Architecture|Requirements)\/\d+\b/;

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: join(EVIDENCE, `${name}.png`), fullPage: true });
}

test.describe('Journey 38: model proposal and history (T-4830)', () => {
  test.setTimeout(600_000);

  test('every Helsinki class draws its form cleanly, History lists the model, Propose waits for an edit', async ({ steward }) => {
    const { page } = steward;
    for (const [width, height] of [[1440, 900], [400, 860]]) {
      await page.setViewportSize({ width, height });
      for (const model of MODELS) {
        await page.goto(`/projects/${PROJECT}/models/${model}?tab=form&lang=en`);
        const panel = page.getByRole('tabpanel');
        const chooser = panel.getByLabel('Class', { exact: true });
        await expect(chooser.or(panel.getByText('This model declares no class yet')).first()).toBeVisible({ timeout: 60_000 });
        const classes = await chooser.locator('option').allTextContents().catch(() => [] as string[]);
        for (const name of classes) {
          await chooser.selectOption({ label: name });
          await expect(panel.locator('form, [role="status"]').first()).toBeVisible({ timeout: 60_000 });
          await expect(panel, `${model} ${name}: no schema error`).not.toContainText(SCHEMA_ERROR);
          await expect(panel, `${model} ${name}: no task id or spec page`).not.toContainText(INTERNAL);
        }
        if (model === MODELS[0]) await shot(page, `model-form-${width}`);

        await page.getByRole('tab', { name: 'History' }).click();
        await expect(page.getByTestId('resource-history-item').first(), `${model}: its published change`).toBeVisible({ timeout: 60_000 });
        if (model === MODELS[0]) await shot(page, `model-history-${width}`);
      }

      await page.goto(`/projects/${PROJECT}/models?edit=${MODELS[0]}&lang=en`);
      const propose = page.getByRole('button', { name: /^Propose v\d+\.\d+\.\d+$/ });
      await expect(propose).toBeVisible({ timeout: 60_000 });
      await expect(propose).toHaveAttribute('aria-disabled', 'true');
      await expect(propose).toHaveAccessibleDescription('Nothing to propose: edit the model first.');
      const overflow = await page.evaluate(() => (document.scrollingElement?.scrollWidth ?? 0) - window.innerWidth);
      expect(overflow, `no sideways scroll at ${width} px`).toBeLessThanOrEqual(0);
      await shot(page, `model-propose-unchanged-${width}`);
    }
  });
});

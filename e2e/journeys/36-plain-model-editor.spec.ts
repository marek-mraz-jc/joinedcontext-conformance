import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect, openProject, openView } from '../fixtures/portal.js';

/**
 * Journey 36 (T-4829): one plain-language data model editor. A steward opens the KPI model from the
 * sidebar, reads each type as a table of fields in plain words, finds no `Any` box and no
 * value-object box in the diagram, adds a field through the space's Add field dialog without
 * seeing an IRI, and proposes it; the proposal is rejected again, so dev keeps nothing. Add type
 * offers Smart Data Models first and keeps IRIs under Advanced. The viewer reads the same page
 * with no Add field. With `JC_EVIDENCE_DIR` set it keeps the 1440 px and 400 px screenshots there.
 */
const PROJECT = process.env.PORTAL_MODELS_PROJECT || 'helsinki';
const MODEL = process.env.PORTAL_MODELS_KPI || 'helsinki-kpi';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const SUFFIX = new Date().toISOString().slice(11, 16).replace(':', '');

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: join(EVIDENCE, `${name}.png`), fullPage: true });
}

/** From the sidebar on a wide screen; a phone folds the sidebar away, so it opens the address. */
async function openModel(page: Page): Promise<void> {
  if ((page.viewportSize()?.width ?? 1440) < 768) {
    await page.goto(`/projects/${PROJECT}/models/${MODEL}?lang=en`);
    await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible({ timeout: 30_000 });
    return;
  }
  await page.goto('/?lang=en');
  await openProject(page, PROJECT);
  await openView(page, /^Data models$/);
  await page.getByRole('searchbox').or(page.getByPlaceholder(/Search by name/)).first().fill(MODEL);
  await page.getByRole('link', { name: new RegExp(MODEL) }).first().click();
  await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible({ timeout: 30_000 });
}

test.describe('Journey 36: one plain model editor (T-4829)', () => {
  test.setTimeout(180_000);

  test('a steward reads types in plain words and proposes a field without an IRI', async ({ steward }) => {
    const { page } = steward;
    await page.setViewportSize({ width: 1440, height: 900 });
    await openModel(page);

    const diagram = page.getByRole('group', { name: "The model's classes and what joins them" });
    await expect(diagram).toBeVisible();
    await expect(diagram.getByText('Any', { exact: true }), 'Any is a value, not a box').toHaveCount(0);
    await expect(diagram.getByText('CalculationPeriod', { exact: true }), 'the period is drawn inside its type').toHaveCount(0);
    const fields = page.getByRole('table', { name: /^Fields of / }).first();
    await expect(fields).toBeVisible();
    await expect(page.getByRole('table', { name: /^Fields of / }).getByText(/^(float|string|datetime|uri)$/)).toHaveCount(0);
    await shot(page, 'model-overview-steward-1440');

    await page.getByRole('button', { name: /^Add field to / }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/IRI|slot_uri|class_uri/)).toHaveCount(0);
    await dialog.getByLabel('Name').fill(`note${SUFFIX}`);
    await dialog.getByLabel('Type').selectOption({ label: 'Text' });
    await shot(page, 'model-add-field-steward-1440');
    await dialog.getByRole('button', { name: 'Propose field' }).click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    const notice = page.getByRole('link', { name: /change|proposal/i }).first();
    await expect(notice, 'the field is proposed, not written').toBeVisible();

    // Dev keeps nothing: the proposal is rejected again.
    await notice.click();
    await page.getByRole('button', { name: /^Reject/ }).first().click();
    await page.getByRole('dialog').getByRole('textbox').first().fill('T-4829 journey: proposal only').catch(() => undefined);
    await page.getByRole('dialog').getByRole('button', { name: /^Reject/ }).click().catch(() => undefined);

    // Add type: Smart Data Models first, IRIs only under Advanced.
    await openModel(page);
    await page.getByRole('link', { name: 'Add type' }).click();
    await expect(page.getByRole('button', { name: 'Import a Smart Data Model' }).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByLabel('Class IRI')).toBeHidden();
    await shot(page, 'model-editor-steward-1440');

    await page.setViewportSize({ width: 400, height: 860 });
    await openModel(page);
    const overflow = await page.evaluate(() => (document.scrollingElement?.scrollWidth ?? 0) - window.innerWidth);
    expect(overflow, 'the page does not scroll sideways at 400 px').toBeLessThanOrEqual(0);
    await shot(page, 'model-overview-steward-400');
  });

  test('the viewer reads the model with no Add field and nothing disabled to guess at', async ({ browser, baseURL, signIn }) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string, baseURL);
    for (const [width, height] of [[1440, 900], [400, 860]]) {
      await page.setViewportSize({ width, height });
      await openModel(page);
      await expect(page.getByRole('table', { name: /^Fields of / }).first()).toBeVisible();
      await expect(page.getByRole('button', { name: /^Add field to / })).toHaveCount(0);
      await expect(page.getByRole('link', { name: 'Add type' })).toHaveCount(0);
      await shot(page, `model-overview-viewer-${width}`);
    }
    await context.close();
  });
});

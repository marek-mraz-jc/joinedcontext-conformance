import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/portal.js';

/**
 * Journey 37 (T-4833, PF-58): access is approved by a second person. An organization
 * administrator opens the grant, role, group and service account forms and reads, before any
 * click, that their own change waits for another administrator; no form promises "Approved as
 * soon as you propose it". The journey proposes nothing, so dev keeps nothing. The API half
 * (proposer approve -> 403 for each kind, another administrator -> merged) is the portal's
 * tests/four_eyes_access_tests.rs. With `JC_EVIDENCE_DIR` set it keeps the 1440 px and 400 px
 * screenshots there.
 */
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const FORMS = ['roles', 'groups', 'service-accounts'];

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  await page.screenshot({ path: join(EVIDENCE, `${name}.png`), fullPage: true });
}

test.describe('Journey 37: four eyes on access changes (T-4833)', () => {
  test.setTimeout(180_000);

  for (const width of [1440, 400]) {
    test(`an administrator's own access change waits for a second person at ${width} px`, async ({ browser, baseURL, signIn }) => {
      const user = process.env.PORTAL_ADMIN_USER;
      const pass = process.env.PORTAL_ADMIN_PASSWORD;
      test.skip(!user || !pass, 'PORTAL_ADMIN_USER and PORTAL_ADMIN_PASSWORD name an organization administrator');
      const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB', viewport: { width, height: 900 } });
      const page = await context.newPage();
      await page.goto('/');
      await signIn(page, user as string, pass as string);

      await page.goto('/organization/members/new?lang=en');
      await expect(page.getByText(/A second administrator approves it: nobody approves their own grant\./)).toBeVisible({ timeout: 30_000 });
      await shot(page, `four-eyes-members-${width}`);

      for (const form of FORMS) {
        await page.goto(`/organization/${form}/new?lang=en`);
        const note = page.getByTestId('propose-standing');
        await expect(note, `${form}: the form says who approves`).toHaveText(
          /^Waits for a second administrator: you cannot approve your own change to /,
          { timeout: 30_000 },
        );
        await expect(page.getByText(/Approved as soon as you propose it/)).toHaveCount(0);
        await shot(page, `four-eyes-${form}-${width}`);
      }
      await context.close();
    });
  }
});

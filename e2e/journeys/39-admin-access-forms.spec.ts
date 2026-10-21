import type { Browser, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 39 (T-4832): Administration's access forms in plain words. An organization administrator
 * opens Invite a person, Grant a role, New group, New service account and New role at 1440 and at
 * 400 px. A role and a service account grant start from a preset (Reader first) and show their
 * raw kinds, verbs and CIM 009 operations only under Custom. Each form is filled and checked
 * (dry run) and stops at the review step: the journey proposes nothing, and the test fails if any
 * write other than a dry run leaves the browser.
 */
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 400, height: 860 },
] as const;

async function shot(page: Page, viewport: string, form: string): Promise<void> {
  const dir = path.join(reportDir, 'journey-39');
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${viewport}-${form}.png`), fullPage: true });
}

async function signedIn(browser: Browser, baseURL: string | undefined, user: string, pass: string, viewport: (typeof VIEWPORTS)[number]) {
  const context = await browser.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
    locale: 'en-GB',
    viewport: { width: viewport.width, height: viewport.height },
  });
  const page = await context.newPage();
  await page.goto('/');
  await signIn(page, user, pass, baseURL);
  return { context, page };
}

/** Every write the page sends that is not a dry run: the journey must send none. */
function watchWrites(page: Page): string[] {
  const writes: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (request.method() === 'GET' || !url.includes('/api/v1/')) return;
    if (/dryRun|\/preferences|\/assistant|\/telemetry|\/validate/.test(url)) return;
    writes.push(`${request.method()} ${new URL(url).pathname}`);
  });
  return writes;
}

const suffix = Date.now().toString(36);

test.describe('Journey 39: access forms in plain words (T-4832)', () => {
  for (const viewport of VIEWPORTS) {
    test(`an administrator fills the five access forms up to the review step at ${viewport.width} px`, async ({ browser, baseURL }) => {
      const user = process.env.PORTAL_USER;
      const pass = process.env.PORTAL_PASSWORD;
      test.skip(!user || !pass, 'PORTAL_USER and PORTAL_PASSWORD name the organization administrator');
      test.setTimeout(240_000);
      const { context, page } = await signedIn(browser, baseURL, user as string, pass as string, viewport);
      const writes = watchWrites(page);
      try {
        await page.goto('/organization/people/new?lang=en');
        await page.getByLabel(/^E-mail/).fill(`journey39-${suffix}@example.org`);
        await page.getByLabel(/^First name/).fill('Journey');
        await page.getByLabel(/^Last name/).fill('Thirty-nine');
        await shot(page, viewport.name, 'invite');

        await page.goto('/organization/members/new?lang=en');
        await expect(page.getByRole('heading', { name: 'Grant a role' })).toBeVisible();
        await shot(page, viewport.name, 'grant');

        await page.goto('/organization/groups/new?lang=en');
        await page.getByLabel(/^Group name/).fill(`journey39-${suffix}`);
        await page.getByRole('button', { name: 'Check' }).click();
        await shot(page, viewport.name, 'group');

        await page.goto('/organization/roles/new?lang=en');
        await expect(page.getByRole('radio', { name: 'Reader' })).toBeChecked();
        await expect(page.getByRole('heading', { name: 'Advanced' })).toHaveCount(0);
        await page.getByLabel(/^Role name/).fill(`journey39-${suffix}`);
        await page.getByRole('radio', { name: 'Writer' }).check();
        await page.getByRole('button', { name: 'Check' }).click();
        await shot(page, viewport.name, 'role-preset');
        await page.getByRole('radio', { name: 'Custom' }).check();
        await expect(page.getByRole('heading', { name: 'Advanced' })).toBeVisible();
        await shot(page, viewport.name, 'role-custom');

        await page.goto('/organization/service-accounts/new?lang=en');
        await expect(page.getByRole('radio', { name: 'Reader' })).toBeChecked();
        await expect(page.getByTestId('operations-picker')).toHaveCount(0);
        await page.getByLabel(/^Name/).first().fill(`journey39-${suffix}`);
        await page.getByLabel(/^Purpose/).fill('Journey 39 checks the service account form; never proposed.');
        await page.getByRole('radio', { name: 'Pipeline writer' }).check();
        await page.getByRole('button', { name: 'Check' }).click();
        await shot(page, viewport.name, 'service-account');
        // The client secret is issued by Keycloak after approval; no form shows a secret field.
        await expect(page.getByLabel(/secret/i)).toHaveCount(0);

        expect(writes, 'the journey proposes nothing').toEqual([]);
      } finally {
        await context.close();
      }
    });
  }
});

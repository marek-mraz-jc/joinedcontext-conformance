import type { Page } from '@playwright/test';
import { test, expect, requireEnv } from '../fixtures/portal.js';

/**
 * Journey 19 (T-2743): the realm's login pages are the joinedcontext theme. A person reads what
 * they are signing into and for which organization, a refused password is tied to its field, the
 * way to a new password is there, the four languages read, and every page is axe clean.
 * PF-90, AP-111, UI-82.
 *
 * The update-password step needs an account whose next sign-in asks for a new password, which
 * cannot be replayed: it runs when PORTAL_TEMP_USER and PORTAL_TEMP_PASSWORD name one.
 * JC_LOGIN_START is where a sign-in starts, the Portal by default; an authorization URL runs the
 * journey against a bare realm.
 */
const START = process.env.JC_LOGIN_START || '/';

/** "Sign in to {app} · {organization}" and its three translations, never a realm id. */
const SIGN_IN_HEADER = /^(Sign in to|Prihlásenie:|Přihlášení:|Anmelden:) .+ · .+$/;

async function openLogin(page: Page, locale?: string): Promise<void> {
  await page.goto(START);
  await page.getByLabel(/username or email|používateľské meno|uživatelské jméno|benutzername/i)
    .waitFor({ state: 'visible', timeout: 20_000 });
  if (locale) {
    const url = new URL(page.url());
    url.searchParams.set('ui_locales', locale);
    url.searchParams.set('kc_locale', locale);
    await page.goto(url.toString());
  }
}

async function heading(page: Page): Promise<string> {
  return (await page.getByRole('heading', { level: 1 }).innerText()).replace(/\s+/g, ' ').trim();
}

test.describe('Journey 19: the login pages say what is being signed into (T-2743)', () => {
  test('UI-82 the sign-in page names the Portal and the organization, in each language', async ({
    page,
    expectNoSeriousA11yViolations,
  }) => {
    await openLogin(page);
    const english = await heading(page);
    expect(english, 'the header names the app and the organization').toMatch(/^Sign in to .+ · .+$/);
    expect(await page.title(), 'the tab reads the same as the header').toBe(english);
    const organization = english.split(' · ').pop() as string;
    await expect(page.locator('#kc-header-wrapper'), 'the brand names the organization').toContainText(organization);
    await expectNoSeriousA11yViolations(page, 'login-en');

    for (const locale of ['sk', 'cs', 'de']) {
      await openLogin(page, locale);
      expect(await heading(page), `${locale}: the header is translated`).toMatch(SIGN_IN_HEADER);
      expect(await page.locator('html').getAttribute('lang')).toBe(locale);
      await expectNoSeriousA11yViolations(page, `login-${locale}`);
    }
  });

  test('UI-82 a wrong password is refused next to the field, and the reset page is one link away', async ({
    page,
    expectNoSeriousA11yViolations,
  }) => {
    await openLogin(page, 'en');
    await page.getByLabel(/username or email/i).fill(requireEnv('PORTAL_USER'));
    await page.getByLabel(/password/i).and(page.locator('input[type=password]')).fill(`not-${Date.now()}`);
    await page.getByRole('button', { name: /^sign in$/i }).click();

    const username = page.getByLabel(/username or email/i);
    await expect(username).toHaveAttribute('aria-invalid', 'true');
    const describedBy = await username.getAttribute('aria-describedby');
    expect(describedBy, 'WCAG 3.3.1: the error is tied to the field').toBeTruthy();
    await expect(page.locator(`#${describedBy}`)).toContainText(/not right/i);
    expect(await heading(page), 'the header still names the app').toMatch(SIGN_IN_HEADER);
    await expectNoSeriousA11yViolations(page, 'login-wrong-password');

    await page.getByRole('link', { name: /forgot your password/i }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(/reset your password/i);
    await expect(page.getByRole('link', { name: /back to sign in/i })).toBeVisible();
    await expectNoSeriousA11yViolations(page, 'login-forgot-password');
  });

  test('UI-82 a required new password is asked for on a themed page', async ({
    page,
    expectNoSeriousA11yViolations,
  }) => {
    const user = process.env.PORTAL_TEMP_USER;
    const pass = process.env.PORTAL_TEMP_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_TEMP_USER is unset (a temporary password works once)');
    await openLogin(page, 'en');
    await page.getByLabel(/username or email/i).fill(user as string);
    await page.getByLabel(/password/i).and(page.locator('input[type=password]')).fill(pass as string);
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(/choose a new password/i);
    await expectNoSeriousA11yViolations(page, 'login-update-password');
  });
});

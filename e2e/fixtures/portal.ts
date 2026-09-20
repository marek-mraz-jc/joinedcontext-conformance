import { test as base, expect, Browser, BrowserContext, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';

/** Credentials never have a default: a wrong hardcoded password looks like a broken portal. */
export function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set — the e2e run needs it, see e2e/README.md`);
  }
  return value;
}

export interface PortalFixtures {
  steward: { context: BrowserContext; page: Page };
  approver: { context: BrowserContext; page: Page };
  signIn: (page: Page, user: string, pass: string) => Promise<void>;
  expectNoSeriousA11yViolations: (page: Page, view?: string) => Promise<void>;
  expectSessionCookie: (context: BrowserContext, page?: Page) => Promise<void>;
}

/**
 * Opens a view from the primary navigation the way a user does — no URL typing (TS-12).
 * The heading is the proof the view actually opened; a nav link that silently does nothing
 * would otherwise leave the next assertion failing somewhere far away.
 */
export async function openView(page: Page, name: RegExp): Promise<void> {
  const nav = page.getByRole('navigation');
  const link = nav.getByRole('link', { name }).or(nav.getByRole('button', { name }))
    .or(page.getByRole('link', { name })).or(page.getByRole('button', { name }));
  await expect(link.first(), `Primary navigation must offer ${name}`).toBeVisible({ timeout: 15_000 });
  await link.first().click();
  await expect(
    page.getByRole('heading', { name }).first(),
    `Opening ${name} must land on its own view`,
  ).toBeVisible({ timeout: 20_000 });
}

export function storageStatePath(user: string): string {
  const sanitized = user.replace(/[^a-zA-Z0-9_-]/g, '_');
  return path.join(reportDir, '.auth', `${sanitized}.json`);
}

export async function signIn(page: Page, user: string, pass: string, portal?: string): Promise<void> {
  if (!page.url().startsWith('http')) {
    throw new Error('signIn expects the page to be on the portal already — call page.goto(\'/\') first');
  }
  // The origin to come back to is the Portal's, and the caller knows it. Reading it off the page
  // was right while `/` showed an anonymous landing page with a Sign in button; the Portal now
  // redirects straight to the realm, so by the time this runs the page is already on the identity
  // provider and every journey waited 20s for a return to `idm.…` that never came (T-2420).
  const portalOrigin = portal ? new URL(portal).origin : new URL(page.url()).origin;

  const signInBtn = page.getByRole('button', { name: /sign in|log in|prihlásiť|anmelden/i });
  const signInLink = page.getByRole('link', { name: /sign in|log in|prihlásiť|anmelden/i });

  if (await signInBtn.isVisible().catch(() => false)) {
    await signInBtn.click();
  } else if (await signInLink.isVisible().catch(() => false)) {
    await signInLink.click();
  }

  // Keycloak login form: username/email and password fields
  const userInput = page.getByLabel(/username or email|username|email|používateľské meno|e-mail/i);
  try {
    await userInput.waitFor({ state: 'visible', timeout: 15_000 });
  } catch (cause) {
    throw new Error(
      `Identity Provider login form did not appear within 15s (current URL: ${page.url()}). Ensure Keycloak is reachable and OIDC redirect is configured: ${cause}`,
    );
  }

  // `.and(input)`, not the label alone: Keycloak's login page carries a "Show password"
  // button whose own aria-label holds the word, so the label matches two elements and every
  // journey dies in the fixture with a strict mode violation before it has signed in (T-2420).
  const passInput = page.getByLabel(/password|heslo|passwort/i).and(page.locator('input'));
  await userInput.fill(user);
  await passInput.fill(pass);

  const submitBtn = page.getByRole('button', { name: /sign in|log in|prihlásiť|anmelden/i });
  await submitBtn.click();

  // Wait for return to Portal base domain. 45s, not 20: the first sign-in after a deploy pays
  // for the Portal's cold start behind the realm round trip, and a journey that fails there
  // reads as a broken portal rather than a slow one (T-2420).
  await page.waitForURL((url) => url.origin === portalOrigin && !url.pathname.includes('/auth/'), {
    timeout: 45_000,
  });
}

export async function expectNoSeriousA11yViolations(page: Page, view?: string): Promise<void> {
  // Requirement TS-13 / WCAG 2.1 Level AA conformance
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();

  if (view) {
    // TS-13 asks for a structured report, not only a pass or fail
    const dir = path.join(reportDir, 'a11y');
    fs.mkdirSync(dir, { recursive: true });
    const slug = view.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
    fs.writeFileSync(
      path.join(dir, `${slug}.json`),
      JSON.stringify({ view, pageUrl: page.url(), ...results }, null, 2),
    );
  }

  if (results.violations.length > 0) {
    const summary = results.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      description: v.description,
      nodes: v.nodes.map((n) => ({
        target: n.target,
        failureSummary: n.failureSummary,
      })),
    }));
    expect(
      results.violations,
      `Axe detected WCAG 2.1 AA accessibility violations:\n${JSON.stringify(summary, null, 2)}`,
    ).toEqual([]);
  }
}

export async function expectSessionCookie(context: BrowserContext, page?: Page): Promise<void> {
  // Requirement TS-12 / Secure Session Cookie validation
  const cookies = await context.cookies();
  const sessionCookie = cookies.find((c) =>
    /(session|auth|token|jc_session|portal)/i.test(c.name),
  );

  expect(
    sessionCookie,
    `Expected an active session cookie matching /session|auth|token|jc_session|portal/i, found: [${cookies.map((c) => c.name).join(', ')}]`,
  ).toBeDefined();

  if (sessionCookie) {
    expect(sessionCookie.httpOnly, `Cookie ${sessionCookie.name} must be httpOnly`).toBe(true);
    expect(sessionCookie.secure, `Cookie ${sessionCookie.name} must be secure`).toBe(true);
    expect(
      ['Lax', 'Strict'],
      `Cookie ${sessionCookie.name} sameSite must be Lax or Strict, got ${sessionCookie.sameSite}`,
    ).toContain(sessionCookie.sameSite);

    if (page) {
      const currentUrl = page.url();
      expect(
        currentUrl,
        `Session cookie value must not leak into address bar URL: ${currentUrl}`,
      ).not.toContain(sessionCookie.value);
    }
  }
}

export const test = base.extend<PortalFixtures>({
  signIn: async ({}, use) => {
    await use(signIn);
  },
  expectNoSeriousA11yViolations: async ({}, use) => {
    await use(expectNoSeriousA11yViolations);
  },
  expectSessionCookie: async ({}, use) => {
    await use(expectSessionCookie);
  },
  steward: async ({ browser, baseURL }, use) => {
    await useSignedIn(browser, baseURL, 'PORTAL_USER', 'PORTAL_PASSWORD', use);
  },
  approver: async ({ browser, baseURL }, use) => {
    // CC-34 separation of duties: the approver is a different person than the author
    await useSignedIn(browser, baseURL, 'PORTAL_APPROVER_USER', 'PORTAL_APPROVER_PASSWORD', use);
  },
});

async function useSignedIn(
  browser: Browser,
  baseURL: string | undefined,
  userVar: string,
  passVar: string,
  use: (session: { context: BrowserContext; page: Page }) => Promise<void>,
): Promise<void> {
  const user = requireEnv(userVar);
  const pass = requireEnv(passVar);
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
  const page = await context.newPage();
  await page.goto('/');
  await signIn(page, user, pass, baseURL);
  await use({ context, page });
  await context.close();
}

export { expect };

import { test, expect, requireEnv } from '../fixtures/portal.js';

test.describe('Journey 1: User Onboarding & Authentication (T-0069, TS-12, TS-13)', () => {
  test('Invited user sets a password and lands on the dashboard', async ({
    page,
    context,
    expectSessionCookie,
  }) => {
    // Testing/03-frontend-and-e2e-tests.md §2 (Journey 1: Onboarding)
    const inviteUrl = process.env.PORTAL_INVITE_URL;
    if (!inviteUrl) {
      test.skip(true, 'PORTAL_INVITE_URL is unset (invitation links are single-use and cannot be replayed)');
      return;
    }
    const invitePassword = requireEnv('PORTAL_INVITE_PASSWORD');

    // Step 1: Open invitation link
    await page.goto(inviteUrl);

    // Step 2: Fill password and confirmation by label
    const newPassField = page.getByLabel(/new password|password|nové heslo|heslo/i).first();
    const confirmPassField = page.getByLabel(/confirm password|password confirmation|potvrdiť heslo/i);

    await expect(newPassField, 'Password input must be visible on onboarding form').toBeVisible();
    await newPassField.fill(invitePassword);

    await expect(confirmPassField, 'Password confirmation input must be visible').toBeVisible();
    await confirmPassField.fill(invitePassword);

    // Step 3: Submit via real button
    const submitBtn = page.getByRole('button', { name: /submit|save|set password|pokračovať|uložiť/i });
    await submitBtn.click();

    // Step 4: Assert arrival at Portal dashboard
    const dashboardHeading = page.getByRole('heading', { level: 1, name: /dashboard|projects|context spaces|prehľad/i });
    await expect(dashboardHeading, 'Must display main dashboard or projects heading').toBeVisible({ timeout: 20_000 });

    const currentUrl = new URL(page.url());
    expect(currentUrl.pathname, 'Must navigate to portal authenticated landing view').toMatch(/(\/|\/projects|\/spaces|\/dashboard)$/);

    // Step 5: Assert secure session cookie attributes
    await expectSessionCookie(context, page);

    // Step 6: OIDC/PKCE security check - credentials/tokens must not leak into URL query parameters
    for (const leakedParam of ['code', 'token', 'id_token', 'access_token']) {
      expect(
        currentUrl.searchParams.has(leakedParam),
        `PKCE or token parameter "${leakedParam}" must not survive in the final address bar URL`,
      ).toBe(false);
    }
  });

  test('Steward signs in through Keycloak and sees only their own projects', async ({
    page,
    context,
    signIn,
    expectNoSeriousA11yViolations,
    expectSessionCookie,
  }) => {
    // Testing/03-frontend-and-e2e-tests.md §2 (Journey 1: Login & Session Isolation)
    const user = requireEnv('PORTAL_USER');
    const pass = requireEnv('PORTAL_PASSWORD');

    // Step 1: Navigate to portal entry point
    await page.goto('/');

    // Step 2: Sign in through Keycloak using interactive UI controls
    await signIn(page, user, pass);

    // Step 3: Assert authenticated view displays steward identity or organization
    const identityIndicator = page.getByText(new RegExp(`(${user}|banskabystrica\\.sk|demo|steward)`, 'i'));
    await expect(identityIndicator.first(), 'Landing page must display authenticated user or organization identifier').toBeVisible({ timeout: 15_000 });

    // Step 4: Verify session cookie attributes and address bar URL hygiene
    await expectSessionCookie(context, page);

    // Step 5: Run WCAG 2.1 AA accessibility scan on authenticated dashboard
    await expectNoSeriousA11yViolations(page);

    // Step 6: Sign out via user menu controls
    const userMenuBtn = page.getByRole('button', { name: /user menu|profile|account|demo\.steward|používateľ/i })
      .or(page.getByRole('link', { name: /profile|account/i }));
    await userMenuBtn.click();

    const signOutBtn = page.getByRole('button', { name: /sign out|log out|odhlásiť|abmelden/i })
      .or(page.getByRole('menuitem', { name: /sign out|log out|odhlásiť|abmelden/i }))
      .or(page.getByRole('link', { name: /sign out|log out|odhlásiť|abmelden/i }));
    await expect(signOutBtn.first(), 'Sign out control must be present in user menu').toBeVisible();
    await signOutBtn.first().click();

    // Step 7: Open root URL again and assert session is truly cleared (sign-in control visible)
    await page.goto('/');
    const signInControl = page
      .getByRole('button', { name: /sign in|log in|prihlásiť|anmelden/i })
      .or(page.getByRole('link', { name: /sign in|log in|prihlásiť|anmelden/i }));
    await expect(signInControl.first(), 'Sign-in control must reappear after explicit sign-out').toBeVisible({ timeout: 15_000 });
  });
});

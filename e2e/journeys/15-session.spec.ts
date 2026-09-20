import { test, expect, openView, requireEnv, signIn } from '../fixtures/portal.js';

/**
 * Journey 15 (T-0361): the browser half of the smoke's token checks — the session cookie is
 * hardened, sign-out really ends the Keycloak session, and an expired session asks for a login
 * instead of showing a blank shell. TS-12, PF-45, PF-46, UI-16.
 *
 * This spec asserts hardening; it never relaxes a setting to make a step pass.
 */
test.describe('Journey 15: session hardening, sign-out and expiry (T-0361)', () => {
  test('PF-45 the session cookie is HttpOnly, Secure and SameSite, and no token is stored client-side', async ({
    steward,
    expectSessionCookie,
  }) => {
    const { context, page } = steward;
    await expectSessionCookie(context, page);

    // A token in localStorage or sessionStorage is reachable by any script on the page; the
    // portal keeps the session in the encrypted cookie precisely so that never happens.
    const stored = await page.evaluate(() => {
      const dump = (storage: Storage) =>
        Object.keys(storage).map((key) => `${key}=${storage.getItem(key) ?? ''}`);
      return [...dump(window.localStorage), ...dump(window.sessionStorage)].join('\n');
    });
    expect(
      stored,
      'PF-46: no access token, id token or refresh token may be persisted in browser storage',
    ).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./);

    expect(page.url(), 'no token may appear in the address bar').not.toMatch(/(access_token|id_token)=/);
  });

  test('CC-42 signing out ends the session at the identity provider', async ({ steward }) => {
    const { page } = steward;

    await page.getByRole('button', { name: /sign out|odhlásiť|abmelden/i })
      .or(page.getByRole('link', { name: /sign out|odhlásiť|abmelden/i }))
      .first()
      .click();

    // After the sign-out the protected shell must not come back on its own: the browser is sent
    // to the identity provider or to the portal's own login view.
    await page.goto('/');
    await expect(
      page.getByRole('button', { name: /sign in|prihlásiť|anmelden/i })
        .or(page.getByLabel(/username or email|username|používateľské meno/i))
        .first(),
      'CC-42: after sign-out a protected view must ask for a login again',
    ).toBeVisible({ timeout: 30_000 });
  });

  test('UI-16 an expired session shows a re-login prompt, not a blank shell or a raw 401', async ({
    browser,
    baseURL,
  }) => {
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, requireEnv('PORTAL_USER'), requireEnv('PORTAL_PASSWORD'));
    await openView(page, /approvals|schválenia/i);

    // The session is ended the way time would end it: the cookie is dropped from the browser.
    // The realm's own token lifetime stays untouched — this spec never edits the portal or the
    // client to make its own assertion easier.
    const session = (await context.cookies()).filter((cookie) => /session|jc_/i.test(cookie.name));
    expect(session.length, 'there must be a session cookie to expire').toBeGreaterThan(0);
    await context.clearCookies();

    await page.reload();
    const body = await page.locator('body').innerText();
    expect(body.trim().length, 'UI-16: an expired session must not leave a blank shell').toBeGreaterThan(0);
    expect(body, 'UI-16: the raw status must never be what the user reads').not.toMatch(/^\s*401\b/);
    await expect(
      page.getByRole('button', { name: /sign in|prihlásiť|anmelden/i })
        .or(page.getByLabel(/username or email|username|používateľské meno/i))
        .first(),
      'UI-16: an expired session offers a way back in',
    ).toBeVisible({ timeout: 30_000 });

    await context.close();
  });
});

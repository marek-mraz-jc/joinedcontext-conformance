import { test, expect } from './fixtures/portal.js';

test.describe('Portal Handshake & Landing Smoke (T-0068, TS-12, TS-13)', () => {
  test('landing page loads, displays non-empty title, exposes sign-in control, and passes WCAG 2.1 AA a11y', async ({
    page,
    expectNoSeriousA11yViolations,
  }) => {
    // 1. Handshake: open root URL (TS-12)
    const response = await page.goto('/');
    expect(response?.ok() || response?.status() === 304, 'Portal root must respond with success HTTP status').toBe(true);

    // 2. Title assertion: non-empty document title matching Portal brand
    const title = await page.title();
    expect(title.trim().length, 'Document title must not be empty').toBeGreaterThan(0);
    expect(title, 'Document title must identify the JoinedContext Portal').toMatch(/(joinedcontext|portal|context)/i);

    // 3. User-visible controls: sign-in control must be present on landing page (T-0068)
    const signInControl = page
      .getByRole('button', { name: /sign in|log in|prihlásiť|anmelden/i })
      .or(page.getByRole('link', { name: /sign in|log in|prihlásiť|anmelden/i }));
    await expect(signInControl.first(), 'Sign-in button/link must be visible on landing page').toBeVisible();

    // 4. Accessibility audit: zero WCAG 2.1 AA violations on empty/landing state (TS-13, UI-15)
    await expectNoSeriousA11yViolations(page);
  });
});

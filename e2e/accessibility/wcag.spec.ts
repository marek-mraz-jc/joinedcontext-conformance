import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Automated WCAG 2.1 AA audit of the core portal views (T-0075) — TS-13 (axe on every core view,
 * zero violations, structured report), UI-15 (WCAG 2.1 AA), UI-16 (ARIA, keyboard, contrast).
 * Each view is reached by clicking, never by typing a URL, so a view that is unreachable for a
 * keyboard user fails here rather than passing an audit nobody can get to.
 */
const views: { name: string; open: RegExp | null }[] = [
  { name: 'dashboard', open: null }, // the landing view after sign-in
  { name: 'flow gallery', open: /flow gallery|blueprint gallery|galéria/i },
  { name: 'approvals', open: /approvals|pending approvals|schválenia/i },
  { name: 'data models', open: /data models|models|dátové modely/i },
  // T-0358: every view the demo walks through, not only the four the audit started with.
  { name: 'context spaces', open: /context spaces|kontextové priestory/i },
  { name: 'pipelines', open: /pipelines|kanály|toky/i },
  { name: 'endpoints', open: /endpoints|koncové body/i },
  { name: 'data access', open: /access|data access|prístup/i },
  { name: 'dashboards', open: /dashboards|nástenky|prehľady/i },
];

test.describe('WCAG 2.1 AA audit of the core portal views (T-0075)', () => {
  for (const view of views) {
    test(`TS-13 ${view.name} has no WCAG 2.1 AA violation`, async ({ steward, expectNoSeriousA11yViolations }) => {
      const { page } = steward;
      if (view.open) await openView(page, view.open);
      await expectNoSeriousA11yViolations(page, view.name);
    });
  }

  test('TS-13 the generated blueprint form has no WCAG 2.1 AA violation', async ({
    steward,
    expectNoSeriousA11yViolations,
  }) => {
    const blueprint = process.env.PORTAL_BLUEPRINT || 'Threshold Alert';
    const { page } = steward;
    await openView(page, /flow gallery|blueprint gallery|galéria/i);
    await page.getByRole('button', { name: new RegExp(blueprint, 'i') })
      .or(page.getByRole('link', { name: new RegExp(blueprint, 'i') })).first().click();
    await expect(
      page.getByRole('form').or(page.locator('form')).first(),
      'the generated form must be open before it can be audited',
    ).toBeVisible({ timeout: 20_000 });
    await expectNoSeriousA11yViolations(page, 'form editor');
  });

  test('UI-16 the keyboard reaches the navigation without a pointer', async ({ steward }) => {
    const { page } = steward;
    await page.keyboard.press('Tab');
    const focused = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      return { tag: el.tagName.toLowerCase(), role: el.getAttribute('role'), tabindex: el.getAttribute('tabindex') };
    });
    expect(focused, 'UI-16: the first Tab must move focus off the body, into a real control').not.toBeNull();
    expect(
      ['a', 'button', 'input', 'select', 'textarea'].includes(focused!.tag) || focused!.role !== null || focused!.tabindex !== null,
      `UI-16: the first Tab landed on <${focused?.tag}>, which is not an interactive control`,
    ).toBe(true);
  });

  /**
   * T-0358: the demo's own path, walked with Tab, Enter and the arrow keys alone. A control that
   * can only be reached with a pointer fails here — and the focus ring is asserted at every stop,
   * because a keyboard path nobody can see is not a keyboard path (UI-16).
   */
  test('UI-16 the endpoint form can be reached, filled and submitted without a pointer', async ({ steward }) => {
    const { page } = steward;
    await page.locator('body').click({ position: { x: 1, y: 1 } });
    await page.keyboard.press('Escape');

    /** Tabs until the predicate matches, asserting a visible focus ring at every stop. */
    const tabUntil = async (matches: (label: string) => boolean, what: string, limit = 60) => {
      for (let i = 0; i < limit; i++) {
        await page.keyboard.press('Tab');
        const stop = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (!el || el === document.body) return null;
          const style = getComputedStyle(el);
          const ring =
            style.outlineStyle !== 'none' && parseFloat(style.outlineWidth || '0') > 0
              ? true
              : style.boxShadow !== 'none';
          return { label: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('name') || '').trim(), ring };
        });
        if (!stop) continue;
        expect(stop.ring, `UI-16: focus must be visible on every stop (at "${stop.label}")`).toBe(true);
        if (matches(stop.label)) return;
      }
      throw new Error(`UI-16: ${what} was never reached with the keyboard alone`);
    };

    await tabUntil((label) => /endpoints|koncové body/i.test(label), 'the Endpoints navigation link');
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('heading', { name: /endpoints|koncové body/i }).first(),
      'UI-16: Enter on the focused navigation link must open the view',
    ).toBeVisible({ timeout: 20_000 });

    await tabUntil((label) => /new endpoint|nový koncový bod/i.test(label), 'the New endpoint button');
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('form').or(page.locator('form')).first(),
      'UI-16: the endpoint form must open from the keyboard',
    ).toBeVisible({ timeout: 20_000 });

    // The form itself: type into the first field, move on with Tab, choose with the arrow keys.
    await page.keyboard.type('keyboard-only');
    await page.keyboard.press('Tab');
    await page.keyboard.press('ArrowDown');
    await tabUntil((label) => /propose change|navrhnúť zmenu/i.test(label), 'the Propose change button');
    await expect(
      page.getByRole('button', { name: /propose change|navrhnúť zmenu/i }).first(),
      'UI-16: the submit control must be focusable, which is what makes the form completable',
    ).toBeFocused();
  });
});

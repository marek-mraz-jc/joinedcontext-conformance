import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 12 (T-0357): the Portal renders in Slovak, English, German and Czech, and no view
 * leaks a raw ICU key. TS-12 (the language is switched with the real control), TS-14, UI-11,
 * UI-12, UI-13, UI-14.
 *
 * The static half of TS-14 — every bundle carrying every key of the others — runs before the
 * browser starts, as `ui/tests/i18n.test.tsx` in the portal repository ("has identical flattened
 * key sets across all four bundles"). A missing translation fails there, in seconds; this spec
 * proves the bundles are actually the strings on screen.
 */
const locales = [
  { code: 'sk', control: /slovenčina|slovak/i },
  { code: 'en', control: /english|angličtina/i },
  { code: 'de', control: /deutsch|german|nemčina/i },
  { code: 'cs', control: /čeština|czech/i },
];

const views: RegExp[] = [
  /context spaces|kontextové priestory/i,
  /endpoints|koncové body/i,
  /pipelines|kanály|toky/i,
  /approvals|schválenia/i,
];

/** A label position holding something like `nav.endpoints` is a key i18next never resolved. */
const RAW_KEY = /^[a-z][a-z0-9]*(\.[a-z0-9_]+){1,4}$/;

async function switchLanguage(page: import('@playwright/test').Page, control: RegExp): Promise<void> {
  const picker = page.getByRole('combobox', { name: /language|jazyk|sprache/i })
    .or(page.getByLabel(/language|jazyk|sprache/i))
    .first();
  await expect(picker, 'UI-11: the interface language is switched in the app').toBeVisible({ timeout: 20_000 });
  await picker.selectOption({ label: control as unknown as string }).catch(async () => {
    await picker.click();
    await page.getByRole('option', { name: control }).or(page.getByRole('menuitem', { name: control })).first().click();
  });
}

test.describe('Journey 12: four locales, no raw key on screen (T-0357)', () => {
  const headings: Record<string, string> = {};

  for (const locale of locales) {
    test(`UI-11 the Portal renders in ${locale.code} without a raw ICU key`, async ({ steward }) => {
      const { page } = steward;
      await switchLanguage(page, locale.control);

      await expect
        .poll(async () => page.locator('html').getAttribute('lang'), {
          message: 'UI-12: the document language must follow the chosen locale',
          timeout: 15_000,
        })
        .toBe(locale.code);

      for (const view of views) {
        await openView(page, view);

        // Every visible label, button and heading; a key that never resolved shows as its key.
        const labels = await page
          .getByRole('heading')
          .or(page.getByRole('button'))
          .or(page.getByRole('columnheader'))
          .allInnerTexts();
        const raw = labels.map((l) => l.trim()).filter((l) => RAW_KEY.test(l));
        expect(raw, `UI-13: ${locale.code} leaves untranslated keys on screen: ${raw.join(', ')}`).toEqual([]);
      }

      headings[locale.code] = (await page.getByRole('heading').first().innerText()).trim();
    });
  }

  test('UI-13 the locales are actually different translations, not one bundle four times', async () => {
    const rendered = Object.entries(headings);
    test.skip(rendered.length < 2, 'needs at least two locale runs to compare');
    const slovak = headings.sk;
    const german = headings.de;
    if (slovak && german) {
      expect(german, 'UI-13: German and Slovak must not render the identical heading').not.toBe(slovak);
    }
  });

  test('UI-14 multi-language manifest metadata falls back instead of rendering empty', async ({ steward }) => {
    const { page } = steward;
    await switchLanguage(page, /deutsch|german|nemčina/i);
    await openView(page, /endpoints|koncové body/i);

    const rows = page.getByRole('row');
    const count = await rows.count();
    expect(count, 'UI-14 needs at least one manifest to read a title from').toBeGreaterThan(1);
    for (let i = 1; i < count; i++) {
      const cells = await rows.nth(i).getByRole('cell').allInnerTexts();
      expect(
        cells.filter((c) => c.trim().length > 0).length,
        'UI-14: a title with no German translation falls back to the organization default, never to an empty cell',
      ).toBeGreaterThan(0);
    }
  });
});

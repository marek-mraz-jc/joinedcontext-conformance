import { test, expect, openView, requireEnv } from '../fixtures/portal.js';

/**
 * Journey 2 (T-0070): browse the flow gallery, instantiate a blueprint through its generated
 * form, and land a pending change proposal — CC-30 (gallery is the primary model), CC-31/UI-01
 * (forms generated from JSON Schema, pickers fed by live gateway queries), CC-32 (saving a form
 * produces the repository change), CC-33 (lifecycle status), TS-12 (real controls only).
 */
test.describe('Journey 2: Blueprint gallery and flow creation (T-0070)', () => {
  const blueprint = process.env.PORTAL_BLUEPRINT || 'Threshold Alert';
  const gallery = /flow gallery|blueprint gallery|galéria/i;

  test('CC-30 the gallery offers the blueprint and opens its generated form', async ({ steward }) => {
    const { page } = steward;
    await openView(page, gallery);

    const card = page.getByRole('button', { name: new RegExp(blueprint, 'i') })
      .or(page.getByRole('link', { name: new RegExp(blueprint, 'i') }))
      .or(page.getByRole('article').filter({ hasText: new RegExp(blueprint, 'i') }).getByRole('button').first());
    await expect(card.first(), `CC-30: the gallery must offer the "${blueprint}" blueprint`).toBeVisible({ timeout: 20_000 });
    await card.first().click();

    // UI-01/CC-31: the form is generated from the parameter schema, so its fields carry real labels
    const form = page.getByRole('form').or(page.locator('form')).first();
    await expect(form, 'UI-01: instantiating a blueprint must render its generated form').toBeVisible({ timeout: 20_000 });
    await expect(
      form.getByRole('textbox').or(form.getByRole('spinbutton')).or(form.getByRole('combobox')).first(),
      'CC-31: a generated form must expose labelled inputs, not a raw manifest editor',
    ).toBeVisible();
  });

  test('CC-31 the entity picker offers only entities the grant allows', async ({ steward }) => {
    const forbidden = process.env.PORTAL_FORBIDDEN_ENTITY_LABEL;
    test.skip(!forbidden, 'PORTAL_FORBIDDEN_ENTITY_LABEL is unset — nothing to prove the narrowing against');
    const { page } = steward;
    await openView(page, gallery);
    await page.getByRole('button', { name: new RegExp(blueprint, 'i') })
      .or(page.getByRole('link', { name: new RegExp(blueprint, 'i') })).first().click();

    const picker = page.getByRole('combobox', { name: /sensor|entity|zariadenie|senzor/i }).first();
    await expect(picker, 'CC-31: the blueprint form must offer a live entity picker').toBeVisible({ timeout: 20_000 });
    await picker.click();

    const options = page.getByRole('option');
    await expect(options.first(), 'CC-31: the picker must be populated from a live gateway query').toBeVisible({ timeout: 20_000 });
    await expect(
      options.filter({ hasText: new RegExp(forbidden!, 'i') }),
      `R9/R11: "${forbidden}" is outside the grant and must never appear in the picker`,
    ).toHaveCount(0);
  });

  test('CC-32 an invalid form is refused and creates no change', async ({ steward }) => {
    const { page } = steward;
    await openView(page, gallery);
    await page.getByRole('button', { name: new RegExp(blueprint, 'i') })
      .or(page.getByRole('link', { name: new RegExp(blueprint, 'i') })).first().click();

    // a threshold is a number: letters must be refused by the generated form, not by the server
    const threshold = page.getByRole('spinbutton').or(page.getByRole('textbox', { name: /threshold|limit|prah/i })).first();
    await expect(threshold, 'the blueprint must expose the threshold parameter').toBeVisible({ timeout: 20_000 });
    await threshold.fill('not-a-number');

    const save = page.getByRole('button', { name: /save|create|deploy|uložiť|vytvoriť/i }).first();
    await save.click();

    await expect(
      page.getByRole('alert').or(page.locator('[aria-invalid="true"]')).first(),
      'UI-01: an invalid value must produce a visible validation error',
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByText(/pending approval|čaká na schválenie/i),
      'CC-32: a refused form must not have produced a change proposal',
    ).toHaveCount(0);
  });

  test('CC-32 saving a valid form produces a change proposal pending approval', async ({ steward }) => {
    const sensor = requireEnv('PORTAL_SENSOR_LABEL');
    const { page } = steward;
    await openView(page, gallery);
    await page.getByRole('button', { name: new RegExp(blueprint, 'i') })
      .or(page.getByRole('link', { name: new RegExp(blueprint, 'i') })).first().click();

    const name = `conformance ${blueprint} ${Date.now()}`;
    await page.getByRole('textbox', { name: /name|title|názov/i }).first().fill(name);

    const picker = page.getByRole('combobox', { name: /sensor|entity|zariadenie|senzor/i }).first();
    await picker.click();
    await page.getByRole('option', { name: new RegExp(sensor, 'i') }).first().click();

    await page.getByRole('spinbutton').or(page.getByRole('textbox', { name: /threshold|limit|prah/i })).first().fill('42');
    await page.getByRole('button', { name: /save|create|deploy|uložiť|vytvoriť/i }).first().click();

    // CC-32: the repository change is produced transparently, with a human-readable description
    const proposal = page.getByRole('article').filter({ hasText: name })
      .or(page.getByRole('row', { name: new RegExp(name, 'i') }))
      .or(page.getByText(name))
      .first();
    await expect(proposal, 'CC-32: saving the form must produce a named change proposal').toBeVisible({ timeout: 30_000 });

    // CC-33: the lifecycle status the user sees, in their words, not the mechanism behind it
    await expect(
      page.getByText(/pending approval|čaká na schválenie/i).first(),
      'CC-33: a saved flow must show the pending-approval status',
    ).toBeVisible({ timeout: 30_000 });
  });
});

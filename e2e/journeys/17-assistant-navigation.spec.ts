import { test, expect, openProject, openView } from '../fixtures/portal.js';

/**
 * Journey 17 (T-0610): the assistant drives the Portal. The steward asks the assistant for an
 * endpoint; the run answers with a `navigate` event, the browser lands on the endpoints view
 * with the form prefilled (UI-45, AG-45), the steward proposes the change, the approver approves
 * it, and the list shows the endpoint. TS-12: real navigation, real controls, no private
 * JavaScript; the assistant is reached the way a person reaches it, through the app builder's
 * conversation.
 *
 * The prompt asks for a publication; what the assistant drafts is the platform's answer, so the
 * assertions read the form rather than dictating its values: a name, a space, at least one
 * representation, all filled before the steward touched anything.
 */
const applications = /^applications$|^aplikácie$|^anwendungen$/i;
const endpoints = /^endpoints$|^koncové body$|^endpunkte$/i;
const approvals = /^approvals$|^schválenia$|^freigaben$/i;
const PROMPT = 'Help me create an endpoint for public air quality data.';

/**
 * The project this journey works in and the endpoint its app reads, by name and as a pair
 * (T-2429): the endpoint has to be one of that project's, because the builder lists the
 * endpoints of the project that is open.
 *
 * A sign-in lands on whichever project comes first for that user, which is not necessarily this
 * one and changes with the seed; the list inside the builder is that project's and its order is
 * its own. The two variables are the ones journeys 8, 11 and 14 already read, with the same
 * defaults, so one installation is described in one place.
 */
const PROJECT = process.env.PORTAL_PROJECT || 'banskabystrica';
const ENDPOINT = process.env.PORTAL_ENDPOINT || 'public-air';

test.describe('Journey 17: the assistant navigates to a prefilled endpoint form (T-0610)', () => {
  test.describe.configure({ mode: 'serial' });

  test('AG-45 UI-45 a request in the conversation lands on the endpoint form, prefilled, and the change is proposed', async ({
    steward,
  }) => {
    const { page } = steward;

    // The project first: the builder reads the endpoints of the project that is open, and the
    // prompt below is written for this one (T-2429).
    await openProject(page, PROJECT);

    // The assistant lives in the builder's conversation: open it the way a person does.
    await openView(page, applications);
    await page.getByRole('button', { name: /generate your own app|vygenerovať|eigene app/i }).first().click();
    const endpointPicker = page.getByLabel(/^endpoint$|^rozhranie$|^endpunkt$/i).first();
    await expect(endpointPicker).toBeVisible({ timeout: 20_000 });
    // By name, never by position: the option's value is the Endpoint's own name, the label is its
    // title in the person's language, and the order is whatever the project's list happens to be.
    await expect(
      endpointPicker.locator(`option[value="${ENDPOINT}"]`),
      `the builder of project ${PROJECT} must offer the endpoint ${ENDPOINT}`,
    ).toHaveCount(1);
    await endpointPicker.selectOption(ENDPOINT);
    await expect(endpointPicker, 'the picked endpoint is what the form holds').toHaveValue(ENDPOINT);
    const submit = page.getByRole('button', { name: /generate the app|vygenerovať aplikáciu|app erzeugen/i }).first();
    await page.getByLabel(/what should the app do|čo má aplikácia robiť|was soll die app/i).first().fill(PROMPT);
    // The button enables once the endpoint, the prompt and the attributes the app may read are
    // all settled — the last of those is derived from the endpoint's own schema surface, so it
    // arrives a moment after the pick. Waiting for the button here names what is missing if it
    // never enables, instead of failing inside the click's own timeout.
    await expect(
      submit,
      'the builder enables Generate once the endpoint, the prompt and the endpoint\'s published attributes are all there',
    ).toBeEnabled({ timeout: 30_000 });
    await submit.click();

    // The run's conversation is live; the same request is sent as a message so the assistant
    // answers it as a share request, not only as an app to build.
    const message = page.getByPlaceholder(/tell the assistant|povedzte asistentovi|sagen sie dem assistenten/i).first();
    await expect(message).toBeVisible({ timeout: 60_000 });
    await message.fill(PROMPT);
    await page.getByRole('button', { name: /^send$|^odoslať$|^senden$/i }).first().click();

    // UI-45: the navigate event moves the Portal without a reload and says so.
    await expect(page).toHaveURL(/\/projects\/[^/]+\/endpoints(\?|$)/, { timeout: 180_000 });
    await expect(
      page.getByRole('status').filter({ hasText: /assistant opened|asistent otvoril|assistent hat/i }).first(),
      'UI-45: a visible notice says the assistant navigated',
    ).toBeVisible({ timeout: 20_000 });

    // The form is open and prefilled from the event, as untrusted input a person reviews.
    const form = page.getByRole('form').or(page.locator('form')).first();
    await expect(form).toBeVisible({ timeout: 20_000 });
    const name = form.getByLabel(/^name$|^názov$/i).first();
    await expect(name).not.toHaveValue('');
    const space = form.getByLabel(/context space|kontextový priestor|kontextraum/i).first();
    await expect(space).not.toHaveValue('');
    const checked = form.getByRole('checkbox', { checked: true });
    expect(await checked.count(), 'at least one representation is prefilled').toBeGreaterThan(0);
    const proposedName = await name.inputValue();
    test.info().annotations.push({ type: 'endpoint', description: proposedName });

    await page.getByRole('button', { name: /propose change|navrhnúť zmenu|änderung vorschlagen/i }).first().click();
    await expect(
      page.getByText(/change|zmena|änderung/i).first(),
      'CC-32: the proposal is a change, not a direct write',
    ).toBeVisible({ timeout: 30_000 });
  });

  test('CC-33 the approver approves the proposed endpoint and the list shows it', async ({ approver }) => {
    const { page } = approver;
    const proposed = test.info().annotations.find((a) => a.type === 'endpoint')?.description;

    // The approver signs in on their own first project, which is not necessarily the one the
    // change was proposed in (T-2429).
    await openProject(page, PROJECT);
    await openView(page, approvals);
    const pending = page.getByRole('row').or(page.getByRole('article')).filter({ hasText: /endpoint|rozhranie|endpunkt/i }).first();
    await expect(pending).toBeVisible({ timeout: 30_000 });
    await pending
      .getByRole('button', { name: /review|open|detail|zobraziť/i })
      .or(pending.getByRole('link', { name: /review|open|detail|zobraziť/i }))
      .first()
      .click();
    await page.getByRole('button', { name: /^approve|schváliť|freigeben/i }).first().click();

    await openView(page, endpoints);
    const rows = page.getByRole('row');
    await expect(
      proposed ? rows.filter({ hasText: proposed }).first() : rows.nth(1),
      'CC-33: an approved endpoint reaches the list on its own',
    ).toBeVisible({ timeout: 120_000 });
  });
});

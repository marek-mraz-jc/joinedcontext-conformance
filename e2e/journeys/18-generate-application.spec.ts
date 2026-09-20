import { test, expect, openView, requireEnv } from '../fixtures/portal.js';

/**
 * Journey 18 (T-0541, AP-50, AP-55, AG-34…AG-42): the owner's story, end to end. A steward asks
 * for an application over a context space, watches it build, opens it, and an anonymous browser
 * asking for the same application is sent to Keycloak rather than served the page.
 *
 * The security half is the point (T-0541): the run reads through the proxy and nothing else, so
 * the conversation must never show a token, a kubeconfig or a Secret's value, and the published
 * application must be behind the login its visibility declares.
 *
 * TS-12: real controls only. The application is generated the way a person generates one, and
 * the anonymous check is a second browser context with no session, not a logged-out flag.
 */
const applications = /^applications$|^aplikácie$|^anwendungen$/i;
const PROMPT =
  'Show the air quality stations on a map, with the latest PM10 value in the popup.';

/** What must never appear in anything the run shows a person (AG-35, AG-40). */
const CREDENTIAL = /(sk-ant-|ghp_|glpat-|-----BEGIN [A-Z ]*PRIVATE KEY|apiVersion:\s*v1[\s\S]{0,80}kind:\s*Config)/;

test.describe('Journey 18: an application is generated, published and protected (T-0541)', () => {
  test.describe.configure({ mode: 'serial' });

  test('AP-50 the steward asks for an application and the run builds it behind the proxy', async ({
    steward,
  }) => {
    const { page } = steward;

    await openView(page, applications);
    await page
      .getByRole('button', { name: /generate your own app|vygenerovať|eigene app/i })
      .first()
      .click();

    const endpointPicker = page.getByLabel(/^endpoint$|^rozhranie$|^endpunkt$/i).first();
    await expect(endpointPicker).toBeVisible({ timeout: 20_000 });
    await endpointPicker.selectOption({ index: 1 });
    await page
      .getByLabel(/what should the app do|čo má aplikácia robiť|was soll die app/i)
      .first()
      .fill(PROMPT);
    await page
      .getByRole('button', { name: /generate the app|vygenerovať aplikáciu|app erzeugen/i })
      .first()
      .click();

    // AP-55: the first frame arrives while the run is still working, so the person is never
    // looking at nothing. The preview is an iframe, not a screenshot of one.
    const preview = page.locator('iframe').first();
    await expect(preview, 'AP-55: a preview appears while the run is still building').toBeVisible({
      timeout: 120_000,
    });

    // The run finishes and says so in its own conversation.
    await expect(
      page.getByText(/ready|hotovo|fertig|live/i).first(),
      'the run reaches a finished state a person can see',
    ).toBeVisible({ timeout: 600_000 });

    // AG-35, AG-40: nothing the workspace held reaches the page. The whole conversation is read
    // as text, because a credential in a tool event is a credential on screen.
    const shown = (await page.locator('body').innerText()) ?? '';
    expect(shown, 'AG-35: no credential appears anywhere in the run the person is shown').not.toMatch(
      CREDENTIAL,
    );

    const name = await page
      .getByRole('heading')
      .filter({ hasText: /./ })
      .first()
      .innerText();
    test.info().annotations.push({ type: 'app', description: name.trim() });
  });

  test('AP-50 the application is served under its own path', async ({ steward }) => {
    const { page } = steward;
    await openView(page, applications);
    const row = page.getByRole('row').or(page.getByRole('article')).nth(1);
    await expect(row).toBeVisible({ timeout: 30_000 });
    await row
      .getByRole('link', { name: /open|otvoriť|öffnen/i })
      .or(row.getByRole('button', { name: /open|otvoriť|öffnen/i }))
      .first()
      .click();
    await expect(page).toHaveURL(/\/apps\/[^/]+\/?/, { timeout: 60_000 });
    test.info().annotations.push({ type: 'url', description: page.url() });
  });

  test('AP-58 an anonymous browser asking for the application is sent to Keycloak', async ({
    browser,
  }) => {
    const url = test.info().annotations.find((a) => a.type === 'url')?.description;
    test.skip(!url, 'the previous test did not reach the application');

    // A fresh context: no storage state, no cookie, nobody.
    const context = await browser.newContext();
    try {
      const page = await context.newPage();
      const response = await page.goto(url as string, { waitUntil: 'domcontentloaded' });

      const issuer = new URL(requireEnv('IDM_URL')).host;
      expect(
        page.url().includes(issuer) || (response?.status() ?? 0) === 401,
        `AP-58: an anonymous visitor is sent to the identity provider or refused, not served the app (landed on ${page.url()})`,
      ).toBeTruthy();

      const body = await page.locator('body').innerText().catch(() => '');
      expect(body, 'AP-58: the application itself is never rendered to a stranger').not.toMatch(
        /PM10|air quality stations/i,
      );
    } finally {
      await context.close();
    }
  });
});

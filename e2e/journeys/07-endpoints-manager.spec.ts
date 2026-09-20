import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 7 (T-0352): DEMO.md step 4 in the browser — the steward creates the public endpoint
 * through the real form and every representation it offers answers. TS-12 (real controls only,
 * no deep link and no slug from a fixture), UI-01, UI-04, EP-01 (one endpoint, several
 * representations), EP-05 (the slug is what serves).
 *
 * The slug is read out of the view after the change is applied, so a form that silently drops a
 * representation, or a slug the platform never publishes, fails here rather than in a later run.
 */
const endpoints = /endpoints|koncové body/i;
const NAME = 'public-air';

/** The endpoint answers only once the change proposal has been approved and reconciled. */
async function waitForRepresentation(
  request: import('@playwright/test').APIRequestContext,
  url: string,
  deadlineMs = 180_000,
): Promise<import('@playwright/test').APIResponse> {
  const until = Date.now() + deadlineMs;
  let last: import('@playwright/test').APIResponse | undefined;
  for (;;) {
    last = await request.get(url, { failOnStatusCode: false });
    if (last.ok()) return last;
    if (Date.now() > until) return last;
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
}

test.describe('Journey 7: the Endpoints manager publishes every representation (T-0352)', () => {
  test('EP-01 a public endpoint is created through the form and its slug serves', async ({
    steward,
    request,
    baseURL,
  }) => {
    const { page } = steward;
    await openView(page, endpoints);

    const existing = page.getByRole('row').filter({ hasText: NAME });
    if ((await existing.count()) === 0) {
      await page.getByRole('button', { name: /new endpoint|nový koncový bod/i }).first().click();

      const form = page.getByRole('form').or(page.locator('form')).first();
      await expect(form, 'UI-04: the endpoint form must open in the app').toBeVisible({ timeout: 20_000 });

      await form.getByLabel(/^name$|^názov$/i).fill(NAME);
      await form.getByLabel(/audience|publikum/i).selectOption({ label: /public|verejn/i as unknown as string }).catch(async () => {
        // A radio group is as valid as a select; the control the app offers is the one clicked.
        await form.getByRole('radio', { name: /public|verejn/i }).check();
      });

      // EP-01: one endpoint carries several representations; both boxes of DEMO step 4 are ticked.
      for (const representation of [/ngsi-?ld/i, /geojson/i]) {
        await form.getByRole('checkbox', { name: representation }).check();
      }

      await page.getByRole('button', { name: /propose change|navrhnúť zmenu/i }).first().click();
      await expect(
        page.getByText(/proposal|change|návrh|zmena/i).first(),
        'UI-23: saving the form must produce a change proposal, never a direct write',
      ).toBeVisible({ timeout: 30_000 });
    }

    // EP-05: the slug the view shows is the address the platform actually serves.
    const row = page.getByRole('row').filter({ hasText: NAME }).first();
    await expect(row, 'the created endpoint must be listed in the view').toBeVisible({ timeout: 120_000 });
    const slug = (await row.innerText()).match(/\b[a-z2-7]{26}\b/)?.[0];
    expect(slug, `the view must show the endpoint's slug; row read: ${await row.innerText()}`).toBeTruthy();

    const base = `${baseURL}/api/endpoint/${slug}`;
    const cases: { path: string; mediaType: RegExp; shape: (body: string) => void }[] = [
      {
        path: '/ngsi-ld/v1/entities?type=AirQualityObserved',
        mediaType: /application\/(ld\+)?json/,
        shape: (body) => expect(JSON.parse(body), 'NGSI-LD answers an array of entities').toBeInstanceOf(Array),
      },
      {
        path: '/file.geojson',
        mediaType: /application\/(geo\+)?json/,
        shape: (body) => expect(JSON.parse(body).type, 'GeoJSON answers a FeatureCollection').toBe('FeatureCollection'),
      },
      {
        path: '/access',
        mediaType: /application\/json/,
        shape: (body) => expect(JSON.parse(body), 'the access document is an object').toBeInstanceOf(Object),
      },
      {
        path: '/schema/v1/json-schema',
        mediaType: /application\/(schema\+)?json/,
        shape: (body) =>
          expect(
            JSON.parse(body).$schema ?? JSON.parse(body).type,
            'the schema representation answers a JSON Schema document',
          ).toBeTruthy(),
      },
    ];

    for (const { path, mediaType, shape } of cases) {
      // The anonymous request carries no token on purpose: the endpoint is public.
      const response = await waitForRepresentation(request, `${base}${path}`);
      expect(response.status(), `${path} must answer from the endpoint`).toBe(200);
      expect(
        response.headers()['content-type'] ?? '',
        `${path} must answer its own media type`,
      ).toMatch(mediaType);
      shape(await response.text());
    }
  });

  test('EP-05 a representation the endpoint does not offer is refused, not guessed', async ({
    steward,
    request,
    baseURL,
  }) => {
    const { page } = steward;
    await openView(page, endpoints);
    const row = page.getByRole('row').filter({ hasText: NAME }).first();
    await expect(row).toBeVisible({ timeout: 60_000 });
    const slug = (await row.innerText()).match(/\b[a-z2-7]{26}\b/)?.[0];
    expect(slug).toBeTruthy();

    const response = await request.get(`${baseURL}/api/endpoint/${slug}/file.parquet`, {
      failOnStatusCode: false,
    });
    expect(
      response.status(),
      'a representation that was never enabled must be refused, never served from a default',
    ).toBeGreaterThanOrEqual(400);
  });
});

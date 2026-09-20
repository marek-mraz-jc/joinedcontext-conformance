import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 8 (T-0353): DEMO.md step 5 in the browser — the steward mints a ServiceAccount key
 * in Project → Access, the dialog shows it once, and the token then writes one entity through
 * the endpoint while the anonymous caller is refused. TS-12 (real controls only), UI-16,
 * PF-36 (the Portal keeps a hash, never the secret), AC-01.
 *
 * Capture is off for this whole spec. The one-time dialog holds the raw key, so a trace, a
 * video or a screenshot of a failed run would be the second read the design exists to prevent.
 * Not scrubbing after the fact: an artifact that was never written cannot leak (T-0353
 * Security). Assertions never interpolate the token either, and the "is it gone" check
 * compares a boolean rather than a substring, because a failed `toContain` prints what it
 * was looking for.
 */
test.use({ trace: 'off', video: 'off', screenshot: 'off' });

const access = /^access$|^prístup$/i;
const project = process.env.PORTAL_PROJECT || 'ovzdusie';

/** `jc_{keyId}_{secret}`: the shape the gateway's api_key verifier splits on (PF-37). */
const KEY_SHAPE = /^jc_[0-9a-f]{16}_[A-Za-z0-9_-]{20,}$/;

test.describe('Journey 8: a ServiceAccount key is shown once and then works (T-0353)', () => {
  test('PF-36 the raw key leaves the Portal exactly once, and the token writes', async ({
    steward,
    request,
    baseURL,
  }) => {
    const { page } = steward;
    await openView(page, access);

    // The account comes from the view, never from a fixture: a spec that knew the name
    // would still pass on a Portal that lists nothing (TS-12).
    const mint = page
      .getByRole('button', { name: /new api key|nový api kľúč/i })
      .first();
    await expect(
      mint,
      `Project → Access must offer a key for a ServiceAccount of project ${project}; ` +
        'DEMO step 5 needs one account with an api-key credential declared in its manifest',
    ).toBeVisible({ timeout: 30_000 });

    await mint.click();

    const dialog = page.getByRole('dialog');
    await expect(
      dialog.getByText(/your new api key|váš nový api kľúč/i),
      'UI-16: minting must answer in a dialog that says this is the only read',
    ).toBeVisible({ timeout: 30_000 });

    const field = dialog.getByLabel(/^api key$|^api kľúč$/i);
    const token = await field.inputValue();
    expect(token, 'the dialog must carry a key of the documented shape').toMatch(KEY_SHAPE);
    const keyId = token.split('_')[1];

    // The copy button is a real control and the journey clicks it, but the assertion is on
    // the field: clipboard permissions differ per browser and the key is what matters.
    await dialog.getByRole('button', { name: /^copy$|^kopírovať$/i }).first().click();
    await dialog.getByRole('button', { name: /i have stored it|uložil som si ho/i }).click();
    await expect(dialog, 'closing the dialog must close it').toBeHidden({ timeout: 15_000 });

    // The secret is unreachable afterwards: not in the DOM, and not after a reload either.
    const afterClose = await page.content();
    expect(
      afterClose.includes(token),
      'the raw key must not survive anywhere in the page after the dialog closes',
    ).toBe(false);

    await page.reload();
    await expect(
      page.getByText(keyId).first(),
      'the key must still be listed by its id, which is the fingerprint a person recognises',
    ).toBeVisible({ timeout: 30_000 });
    const afterReload = await page.content();
    expect(
      afterReload.includes(token),
      'a reload must not produce the secret a second time: the Portal stores a hash (PF-36)',
    ).toBe(false);

    // AC-01: the token is a credential the gateway honours. The write goes through the
    // endpoint the Endpoints view publishes, not through a path this spec invented.
    const slug = await publicEndpointSlug(page);
    const entity = `urn:ngsi-ld:AirQualityObserved:banskabystrica.sk:${project}:e2e-t0353`;
    const write = await request.post(`${baseURL}/api/endpoint/${slug}/ngsi-ld/v1/entities`, {
      headers: {
        authorization: `Bearer ${token}`,
        'content-type': 'application/ld+json',
      },
      data: {
        id: entity,
        type: 'AirQualityObserved',
        pm10: { type: 'Property', value: 12.5 },
        location: {
          type: 'GeoProperty',
          value: { type: 'Point', coordinates: [19.146, 48.736] },
        },
      },
      failOnStatusCode: false,
    });
    expect(
      [201, 204, 409].includes(write.status()),
      `the minted key must be able to write through the endpoint; ` +
        `the gateway answered ${write.status()} (409 is the entity from an earlier run)`,
    ).toBe(true);

    // The same write without the key is refused, in the shape an NGSI-LD client reads.
    const anonymous = await request.patch(
      `${baseURL}/api/endpoint/${slug}/ngsi-ld/v1/entities/${encodeURIComponent(entity)}/attrs`,
      {
        headers: { 'content-type': 'application/json' },
        data: { pm10: { type: 'Property', value: 99 } },
        failOnStatusCode: false,
      },
    );
    expect(
      anonymous.status(),
      'DEMO step 5: the anonymous caller may read and may not write',
    ).toBe(403);
    expect(
      anonymous.headers()['content-type'] ?? '',
      'a refusal is a problem document, not an HTML page',
    ).toMatch(/application\/(problem\+)?json/);
  });
});

/** The slug of the public endpoint, read out of the Endpoints view like journey 7 does. */
async function publicEndpointSlug(page: import('@playwright/test').Page): Promise<string> {
  const name = process.env.PORTAL_ENDPOINT || 'public-air';
  await openView(page, /endpoints|koncové body/i);
  const row = page.getByRole('row').filter({ hasText: name }).first();
  await expect(row, `the Endpoints view must list ${name}`).toBeVisible({ timeout: 60_000 });
  const slug = (await row.innerText()).match(/\b[a-z2-7]{26}\b/)?.[0];
  expect(slug, `the view must show the slug of ${name}`).toBeTruthy();
  return slug as string;
}

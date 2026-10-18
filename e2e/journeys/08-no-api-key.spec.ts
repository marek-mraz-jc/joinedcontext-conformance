import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 8 (T-4617): there are no API keys (owner 2026-10-18, T-4362). Identity is Keycloak
 * only, so the steward's Project → Access offers no key to mint (PF-36, PF-104), and a bearer in
 * the old `jc_{keyId}_{secret}` shape is no credential: the gateway answers it 401 like any token
 * that is not a JWT it verifies, in the problem shape an NGSI-LD client reads (PF-37).
 * Replaces the T-0353 journey that minted a key and wrote with it.
 */
const access = /^access$|^prístup$/i;

/** A bearer of the retired API-key shape; random, so it names no key that ever existed. */
function retiredKey(): string {
  const hex = [...crypto.getRandomValues(new Uint8Array(8))].map((b) => b.toString(16).padStart(2, '0')).join('');
  const secret = Buffer.from(crypto.getRandomValues(new Uint8Array(24))).toString('base64url');
  return `jc_${hex}_${secret}`;
}

test.describe('Journey 8: no API keys, Keycloak only (T-4617)', () => {
  test('PF-36 PF-104 Project → Access offers no API key to mint', async ({ steward }) => {
    const { page } = steward;
    // openView waits for the view's own heading, so an absent control means absent.
    await openView(page, access);
    await expect(
      page.getByRole('button', { name: /api key|api kľúč/i }),
      'the Portal mints no API key any more (T-4362): no control may offer one',
    ).toHaveCount(0);
  });

  test('PF-37 a bearer in the retired jc_ key shape is refused 401 as a problem document', async ({
    steward,
    request,
    baseURL,
  }) => {
    const slug = await publicEndpointSlug(steward.page);
    const entity = 'urn:ngsi-ld:AirQualityObserved:e2e-t4617';
    const write = await request.post(`${baseURL}/api/endpoint/${slug}/ngsi-ld/v1/entities`, {
      headers: { authorization: `Bearer ${retiredKey()}`, 'content-type': 'application/ld+json' },
      data: { id: entity, type: 'AirQualityObserved', pm10: { type: 'Property', value: 12.5 } },
      failOnStatusCode: false,
    });
    expect(write.status(), 'a jc_ bearer is no credential: 401, never a write').toBe(401);
    expect(
      write.headers()['content-type'] ?? '',
      'a refusal is a problem document, not an HTML page',
    ).toMatch(/application\/(problem\+)?json/);
  });
});

/** The slug of the public endpoint, read out of the Endpoints view like journey 7 does. */
async function publicEndpointSlug(page: import('@playwright/test').Page): Promise<string> {
  const name = process.env.PORTAL_ENDPOINT || 'public-air';
  await openView(page, /endpoints|endpointy|koncové body/i);
  const row = page.getByRole('row').filter({ hasText: name }).first();
  await expect(row, `the Endpoints view must list ${name}`).toBeVisible({ timeout: 60_000 });
  const slug = (await row.innerText()).match(/\b[a-z2-7]{26}\b/)?.[0];
  expect(slug, `the view must show the slug of ${name}`).toBeTruthy();
  return slug as string;
}

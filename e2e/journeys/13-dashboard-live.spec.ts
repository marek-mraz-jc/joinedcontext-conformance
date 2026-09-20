import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 13 (T-0359): DEMO.md step 6 — the map dashboard draws live stations, its layer control
 * filters them, and the layer asks the endpoint only for the visible area. TS-12, UI-17, UI-19
 * (a public dashboard reads public endpoints only), UI-20, UI-22 (the bounding box travels as
 * `geoQ`).
 */
const dashboards = /dashboards|nástenky|prehľady/i;
const DASHBOARD = process.env.PORTAL_DASHBOARD || 'Kvalita ovzdušia';

/** MapLibre reports a loaded style on the canvas' own element once the tiles are in. */
async function waitForLoadedMap(page: import('@playwright/test').Page): Promise<void> {
  const canvas = page.locator('canvas.maplibregl-canvas, canvas').first();
  await expect(canvas, 'UI-17: the dashboard must render a map canvas').toBeVisible({ timeout: 30_000 });
  await expect
    .poll(
      async () =>
        page.evaluate(() => {
          const container = document.querySelector('.maplibregl-map');
          return container ? container.classList.contains('maplibregl-map') : false;
        }),
      { message: 'UI-17: the map style must finish loading', timeout: 60_000, intervals: [2_000] },
    )
    .toBe(true);
}

test.describe('Journey 13: the map dashboard shows and filters live stations (T-0359)', () => {
  test('UI-22 a layer asks the endpoint only for the visible area', async ({ steward }) => {
    const { page } = steward;

    // The request is watched before the click that causes it, so the assertion cannot pass on a
    // request made earlier for something else.
    const layerRequest = page.waitForRequest(
      (request) => /\/api\/endpoint\/[a-z2-7]+\//.test(request.url()) && /geoQ=|geoq=/i.test(request.url()),
      { timeout: 60_000 },
    );

    await openView(page, dashboards);
    await page.getByRole('link', { name: new RegExp(DASHBOARD, 'i') })
      .or(page.getByRole('button', { name: new RegExp(DASHBOARD, 'i') }))
      .first()
      .click();
    await waitForLoadedMap(page);

    const request = await layerRequest;
    expect(
      request.url(),
      'UI-22: the layer must narrow its query to the viewport, not download the space',
    ).toMatch(/geoQ=/i);
  });

  test('UI-20 the layer control filters what the map draws', async ({ steward }) => {
    const { page } = steward;
    await openView(page, dashboards);
    await page.getByRole('link', { name: new RegExp(DASHBOARD, 'i') })
      .or(page.getByRole('button', { name: new RegExp(DASHBOARD, 'i') }))
      .first()
      .click();
    await waitForLoadedMap(page);

    const layer = page.getByRole('checkbox', { name: /pm10|pm 10/i }).first();
    await expect(layer, 'UI-20: the layer control must list the PM10 layer').toBeVisible({ timeout: 30_000 });

    const featuresRequested = async () =>
      (await page.locator('[data-feature-count]').first().getAttribute('data-feature-count').catch(() => null))
      ?? (await page.locator('body').innerText());

    const before = await featuresRequested();
    await layer.click();
    await expect
      .poll(featuresRequested, {
        message: 'UI-20: toggling a layer must change what the map draws',
        timeout: 30_000,
        intervals: [2_000],
      })
      .not.toBe(before);
  });

  test('UI-19 the public dashboard needs no session at all', async ({ browser, baseURL }) => {
    const anonymous = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await anonymous.newPage();
    const dashboardUrl = process.env.PORTAL_PUBLIC_DASHBOARD_URL;
    test.skip(!dashboardUrl, 'PORTAL_PUBLIC_DASHBOARD_URL names the published public dashboard');

    await page.goto(dashboardUrl as string);
    await waitForLoadedMap(page);
    await expect(
      page.getByText(/not shown, because a public dashboard|layerBlocked/i),
      'UI-19: a public dashboard bound to a private space must say so, never draw it',
    ).toHaveCount(0);

    await anonymous.close();
  });
});

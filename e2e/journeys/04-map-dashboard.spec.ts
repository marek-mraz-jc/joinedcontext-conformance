import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 4 (T-0072): spatial dashboards, vector layer rendering with MapLibre GL JS,
 * geoQ bounding box queries, native rendering thresholds, layer toggling, feature popups,
 * and endpoint authorization — TS-12, UI-20, UI-21, UI-22, CC-37.
 */
test.describe('Journey 4: Spatial dashboard, vector layers and geoQ (T-0072)', () => {
  const dashboardName = process.env.PORTAL_DASHBOARD || 'Air Quality';
  const layerName = process.env.PORTAL_MAP_LAYER || 'Sensors';
  const dashboardsNav = /dashboards|nástenky|map|mapa|prehľad/i;

  async function navigateToMapDashboard(page: import('@playwright/test').Page): Promise<void> {
    await openView(page, dashboardsNav);
    const dashboardCard = page.getByRole('button', { name: new RegExp(dashboardName, 'i') })
      .or(page.getByRole('link', { name: new RegExp(dashboardName, 'i') }))
      .or(page.getByRole('article').filter({ hasText: new RegExp(dashboardName, 'i') }).getByRole('button').first());
    await expect(dashboardCard.first(), `UI-20: dashboard gallery must list "${dashboardName}"`).toBeVisible({ timeout: 20_000 });
    await dashboardCard.first().click();
    await expect(page.locator('canvas').first(), 'UI-20: opening spatial dashboard must mount a canvas').toBeVisible({ timeout: 25_000 });
  }

  test('UI-20 opening the dashboard renders a painted MapLibre GL WebGL canvas', async ({ steward }) => {
    const { page } = steward;
    await navigateToMapDashboard(page);

    const canvas = page.locator('canvas').first();
    await expect(canvas, 'UI-20: MapLibre GL map must render an HTML5 canvas element').toBeVisible({ timeout: 20_000 });

    const box = await canvas.boundingBox();
    expect(box, 'UI-20: map canvas must have rendered bounding dimensions').not.toBeNull();
    // A 1x1 or unstyled 0x0 element is unpainted and fails the rendering requirement
    expect(box!.width, 'UI-20: map canvas must have painted width > 200px').toBeGreaterThan(200);
    expect(box!.height, 'UI-20: map canvas must have painted height > 200px').toBeGreaterThan(200);

    const hasWebGL = await page.evaluate(() => {
      const el = document.querySelector('canvas');
      if (!el) return false;
      const gl = el.getContext('webgl2') || el.getContext('webgl');
      return gl !== null && !gl.isContextLost();
    });
    expect(hasWebGL, 'UI-20: MapLibre GL canvas must hold an active WebGL or WebGL2 context').toBe(true);
  });

  test('UI-22 the map asks the endpoint for GeoJSON inside a bounding box', async ({ steward }) => {
    const { page } = steward;

    const geoRequestPromise = page.waitForRequest((req) => {
      if (!req.url().includes('file.geojson')) return false;
      try {
        const url = new URL(req.url());
        return url.searchParams.has('geoQ');
      } catch {
        return false;
      }
    }, { timeout: 30_000 });

    await navigateToMapDashboard(page);
    const req = await geoRequestPromise;
    const url = new URL(req.url());
    const geoQ = url.searchParams.get('geoQ') || '';

    // UI-22 requires endpoint spatial filtering via NGSI-LD geoQ spatial operators
    const validOperators = ['near', 'within', 'intersects', 'contains', 'overlaps', 'equals', 'disjoint'];
    const matchedOp = validOperators.some((op) => geoQ.toLowerCase().includes(op));
    expect(
      matchedOp,
      `UI-22: geoQ query parameter must specify a spatial operator, got: "${geoQ}"`,
    ).toBe(true);

    const response = await req.response();
    expect(response, 'UI-22: geoQ request must receive a response').not.toBeNull();
    expect(response!.status(), 'UI-22: endpoint must respond with HTTP 200').toBe(200);
    const contentType = response!.headers()['content-type'] || '';
    expect(
      contentType.includes('json'),
      `UI-22: content-type must specify GeoJSON/JSON, got "${contentType}"`,
    ).toBe(true);
  });

  test('UI-22 panning or zooming the map re-queries with a different bounding box', async ({ steward }) => {
    const { page } = steward;

    const firstReqPromise = page.waitForRequest((req) => {
      return req.url().includes('file.geojson') && new URL(req.url()).searchParams.has('geoQ');
    }, { timeout: 30_000 });

    await navigateToMapDashboard(page);
    const firstReq = await firstReqPromise;
    const firstGeoQ = new URL(firstReq.url()).searchParams.get('geoQ') || '';

    const secondReqPromise = page.waitForRequest((req) => {
      if (!req.url().includes('file.geojson')) return false;
      const currentGeoQ = new URL(req.url()).searchParams.get('geoQ') || '';
      return currentGeoQ !== '' && currentGeoQ !== firstGeoQ;
    }, { timeout: 30_000 });

    const zoomInBtn = page.getByRole('button', { name: /zoom in|priblížiť|\+/i }).first();
    await expect(zoomInBtn, 'UI-22: map zoom control must be visible').toBeVisible({ timeout: 15_000 });
    await zoomInBtn.click();

    const secondReq = await secondReqPromise;
    const secondGeoQ = new URL(secondReq.url()).searchParams.get('geoQ') || '';
    expect(
      secondGeoQ,
      'UI-22: zooming the map must update the viewport bounding box geoQ constraint',
    ).not.toEqual(firstGeoQ);
  });

  test('UI-21 a layer below the 50,000-feature threshold renders natively', async ({ steward }) => {
    const { page } = steward;
    await navigateToMapDashboard(page);

    // Feature count badge / status display
    const countBadge = page.getByRole('status')
      .or(page.locator('[data-testid="feature-count"]'))
      .or(page.getByText(/\d[\d\s.,]*\s*(features|prvk)/i))
      .first();
    await expect(countBadge, 'UI-21: map view must report feature count for active layers').toBeVisible({ timeout: 20_000 });

    const badgeText = (await countBadge.textContent()) || '';
    const rawNumber = badgeText.replace(/[^\d]/g, '');
    const count = parseInt(rawNumber, 10);
    expect(Number.isFinite(count), `UI-21: feature count must parse to a number, got "${badgeText}"`).toBe(true);
    expect(count, 'UI-21: dataset under test must be below the 50,000 threshold for native styling').toBeLessThan(50_000);

    // UI-21 names the engine, so the portal has to say which one it used: the marker attribute when it
    // publishes one, otherwise the observable signature — deck.gl stacks its own canvas over the map.
    const engineLabel = page.locator('[data-render-engine]');
    if ((await engineLabel.count()) > 0) {
      expect(
        (await engineLabel.first().getAttribute('data-render-engine'))?.toLowerCase(),
        'UI-21: a layer under the threshold must report the native MapLibre vector engine',
      ).toMatch(/maplibre|vector/i);
    } else {
      await expect(
        page.locator('canvas'),
        'UI-21: a layer under the threshold renders natively — a second, stacked deck.gl canvas means the WebGL path was taken',
      ).toHaveCount(1);
    }
  });

  test('UI-22 switching the layer off and on re-queries the endpoint', async ({ steward }) => {
    const { page } = steward;
    await navigateToMapDashboard(page);

    const layerToggle = page.getByRole('checkbox', { name: new RegExp(layerName, 'i') })
      .or(page.getByRole('switch', { name: new RegExp(layerName, 'i') }))
      .or(page.getByLabel(new RegExp(layerName, 'i')))
      .first();
    await expect(layerToggle, `UI-22: layer control must present a toggle for layer "${layerName}"`).toBeVisible({ timeout: 15_000 });

    // Switch off
    await layerToggle.click();

    let newGeoRequests = 0;
    const requestListener = (req: import('@playwright/test').Request) => {
      if (req.url().includes('file.geojson')) newGeoRequests++;
    };
    page.on('request', requestListener);

    // Switch back on — must trigger a new GeoJSON fetch for the re-enabled layer
    const nextReqPromise = page.waitForRequest((req) => req.url().includes('file.geojson'), { timeout: 20_000 });
    await layerToggle.click();
    await nextReqPromise;

    page.off('request', requestListener);
    expect(newGeoRequests, 'UI-22: toggling layer back on must issue a fresh GeoJSON request').toBeGreaterThanOrEqual(1);
  });

  test('UI-20 feature popup shows a human name and never a raw URN', async ({ steward }) => {
    const { page } = steward;
    await navigateToMapDashboard(page);

    const canvas = page.locator('canvas').first();
    const box = await canvas.boundingBox();
    expect(box, 'UI-20: map canvas must have bounding box').not.toBeNull();

    // Click centre of canvas to inspect feature
    await canvas.click({ position: { x: box!.width / 2, y: box!.height / 2 } });

    const popup = page.getByRole('dialog')
      .or(page.locator('.maplibregl-popup'))
      .or(page.locator('[data-testid="feature-popup"]'))
      .first();
    await expect(popup, 'UI-20: clicking a feature must open an informational popup').toBeVisible({ timeout: 15_000 });

    const popupText = (await popup.textContent()) || '';
    expect(popupText.length, 'UI-20: popup must contain descriptive feature text').toBeGreaterThan(0);
    // CC-37 requires human-friendly names in user-facing views rather than raw NGSI-LD URNs
    expect(
      popupText,
      `CC-37: popup must show human title/name, not raw URN. Found text: "${popupText}"`,
    ).not.toMatch(/urn:ngsi-ld:/i);
  });

  test('UI-19/EP-16 a private layer refuses an unauthenticated request', async ({ browser, steward }) => {
    const privateDashboard = process.env.PORTAL_PRIVATE_DASHBOARD;
    test.skip(!privateDashboard, 'PORTAL_PRIVATE_DASHBOARD is unset — skipping unauthorized layer check');

    const { page } = steward;
    let privateGeoJsonUrl = '';

    const capturedRequest = page.waitForRequest((req) => {
      if (req.url().includes('file.geojson')) {
        privateGeoJsonUrl = req.url();
        return true;
      }
      return false;
    }, { timeout: 30_000 });

    await openView(page, dashboardsNav);
    const card = page.getByRole('button', { name: new RegExp(privateDashboard!, 'i') })
      .or(page.getByRole('link', { name: new RegExp(privateDashboard!, 'i') })).first();
    await expect(card, `UI-19: private dashboard "${privateDashboard}" must be listed`).toBeVisible({ timeout: 20_000 });
    await card.click();

    await capturedRequest;
    expect(privateGeoJsonUrl, 'UI-19: private dashboard must have issued a GeoJSON endpoint request').toBeTruthy();

    // Replay endpoint request from anonymous context without authorization cookies/headers
    const anonContext = await browser.newContext();
    const anonResponse = await anonContext.request.get(privateGeoJsonUrl);
    const status = anonResponse.status();
    const body = await anonResponse.text();
    await anonContext.close();

    expect(
      [401, 403, 404],
      `UI-19/EP-16: a layer bound to a non-public endpoint must fail closed with 401, 403 or 404, got ${status}`,
    ).toContain(status);
    expect(body, 'UI-19/EP-16: a refused endpoint response must carry no feature collection').not.toContain('"features"');
  });
});

import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 5 (T-0073): dense layer visualization engine selection, deck.gl WebGL overlay,
 * spatial aggregations (hexbins/grid), WebGL context survival, and interaction stability
 * under heavy data volumes — TS-12, UI-20, UI-21.
 */
/**
 * What every canvas on the page reports about its WebGL context. deck.gl draws through WebGL, so a
 * canvas whose context is lost or whose drawing buffer collapsed to 0x0 is the exact symptom of the
 * crash UI-20 forbids — and it is observable without reaching into deck.gl internals.
 */
async function webglReport(page: import('@playwright/test').Page): Promise<{
  canvases: number;
  alive: number;
  lost: number;
}> {
  return page.evaluate(() => {
    const canvases = Array.from(document.querySelectorAll('canvas'));
    let alive = 0;
    let lost = 0;
    for (const canvas of canvases) {
      const gl = (canvas.getContext('webgl2') || canvas.getContext('webgl')) as WebGLRenderingContext | null;
      if (!gl) continue;
      if (gl.isContextLost()) lost++;
      else if (gl.drawingBufferWidth > 0 && gl.drawingBufferHeight > 0) alive++;
    }
    return { canvases: canvases.length, alive, lost };
  });
}

test.describe('Journey 5: deck.gl WebGL rendering of a dense layer (T-0073)', () => {
  const denseDashboard = process.env.PORTAL_DENSE_DASHBOARD;
  const dashboardsNav = /dashboards|nástenky|map|mapa|prehľad/i;

  test.beforeEach(() => {
    test.skip(
      !denseDashboard,
      'PORTAL_DENSE_DASHBOARD is unset — deck.gl journey requires a dataset with ≥ 50,000 features',
    );
  });

  async function openDenseDashboard(page: import('@playwright/test').Page): Promise<void> {
    await openView(page, dashboardsNav);
    const card = page.getByRole('button', { name: new RegExp(denseDashboard!, 'i') })
      .or(page.getByRole('link', { name: new RegExp(denseDashboard!, 'i') }))
      .or(page.getByRole('article').filter({ hasText: new RegExp(denseDashboard!, 'i') }).getByRole('button').first());
    await expect(card, `UI-21: gallery must offer dense dashboard "${denseDashboard}"`).toBeVisible({ timeout: 20_000 });
    await card.click();
    await expect(page.locator('canvas').first(), 'UI-20: spatial dashboard must mount a canvas').toBeVisible({ timeout: 30_000 });
  }

  test('UI-21 dense layer with ≥ 50,000 features activates the deck.gl WebGL overlay', async ({ steward }) => {
    const { page } = steward;
    await openDenseDashboard(page);

    // Verify feature count threshold
    const countBadge = page.getByRole('status')
      .or(page.locator('[data-testid="feature-count"]'))
      .or(page.getByText(/\d[\d\s.,]*\s*(features|prvk)/i))
      .first();
    await expect(countBadge, 'UI-21: dense dashboard must display the feature count').toBeVisible({ timeout: 20_000 });

    const badgeText = (await countBadge.textContent()) || '';
    const count = parseInt(badgeText.replace(/[^\d]/g, ''), 10);
    expect(Number.isFinite(count), `UI-21: feature count must be numeric, got "${badgeText}"`).toBe(true);
    expect(
      count,
      `UI-21: dense dataset must have at least 50,000 features, found: ${count}`,
    ).toBeGreaterThanOrEqual(50_000);

    // Verify deck.gl engine activation: either a stacked overlay canvas or explicit deck.gl engine marker
    const overlayCanvas = page.locator('canvas#deckgl-overlay, canvas[id*="deck"], canvas.deckgl-overlay');
    const totalCanvases = await page.locator('canvas').count();
    const engineMarker = page.locator('[data-render-engine*="deck" i], [data-render-engine*="webgl" i]');

    const hasDeckOverlay = (await overlayCanvas.count()) > 0 || totalCanvases >= 2 || (await engineMarker.count()) > 0;
    expect(
      hasDeckOverlay,
      `UI-21: layers with ≥ 50,000 features must render using deck.gl WebGL layer, found ${totalCanvases} canvases`,
    ).toBe(true);
  });

  test('UI-20 deck.gl overlay maintains an active, unlost WebGL context', async ({ steward }) => {
    const { page } = steward;
    await openDenseDashboard(page);

    const gl = await webglReport(page);
    expect(gl.canvases, 'UI-20: the dense dashboard must mount at least one canvas').toBeGreaterThan(0);
    expect(
      gl.alive,
      `UI-20: at least one canvas must hold a live WebGL context with a painted drawing buffer (${JSON.stringify(gl)})`,
    ).toBeGreaterThan(0);
    expect(gl.lost, `UI-20: no WebGL context may be lost (${JSON.stringify(gl)})`).toBe(0);
  });

  test('UI-21 spatial aggregation view renders without errors or context loss', async ({ steward }) => {
    const { page } = steward;

    const runtimeErrors: string[] = [];
    page.on('pageerror', (err) => runtimeErrors.push(`[PageError] ${err.message}`));
    page.on('console', (msg) => {
      if (msg.type() === 'error') runtimeErrors.push(`[ConsoleError] ${msg.text()}`);
    });

    await openDenseDashboard(page);

    const aggregationControl = page.getByRole('button', { name: /hexagon|heatmap|grid|hex|agregácia/i })
      .or(page.getByRole('radio', { name: /hexagon|heatmap|grid|hex|agregácia/i }))
      .or(page.getByRole('combobox', { name: /aggregation|agregácia|render mode/i }))
      .first();
    await expect(
      aggregationControl,
      'UI-21: dense visualization must expose an aggregation control (hexbins/grid/heatmap)',
    ).toBeVisible({ timeout: 20_000 });
    await aggregationControl.click();

    // the aggregation switch is where a WebGL renderer dies: the context survives or it does not
    const gl = await webglReport(page);
    expect(
      gl.alive,
      `UI-21: the WebGL context must stay live after switching to the aggregation view (${JSON.stringify(gl)})`,
    ).toBeGreaterThan(0);
    expect(gl.lost, `UI-21: the aggregation view must not lose a WebGL context (${JSON.stringify(gl)})`).toBe(0);
    expect(
      runtimeErrors,
      `UI-21: aggregation layer rendering caused uncaught errors:\n${runtimeErrors.join('\n')}`,
    ).toEqual([]);
  });

  test('UI-20 map remains responsive and crash-free during interaction burst', async ({ steward }) => {
    const { page } = steward;

    const runtimeErrors: string[] = [];
    page.on('pageerror', (err) => runtimeErrors.push(`[PageError] ${err.message}`));

    await openDenseDashboard(page);

    const zoomInBtn = page.getByRole('button', { name: /zoom in|priblížiť|\+/i }).first();
    await expect(zoomInBtn, 'UI-20: zoom button must be available').toBeVisible({ timeout: 15_000 });

    // Interaction burst: 3 consecutive zoom clicks and pan via keyboard
    await zoomInBtn.click();
    await zoomInBtn.click();
    await zoomInBtn.click();

    const canvas = page.locator('canvas').first();
    await canvas.focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowDown');

    // UI-20 / TS-12 memory safety & stability: headless CI boxes lack dedicated GPUs and have variable
    // frame pacing, so wall-clock FPS and Chromium-only performance.memory are excluded to prevent test flakiness.
    // Instead we assert the browser remains connected, controls remain interactive, and WebGL context is not lost.
    expect(page.context().browser()?.isConnected(), 'UI-20: browser connection must stay active after heavy map load').toBe(true);
    await expect(zoomInBtn, 'UI-20: map controls must remain interactive after zoom/pan burst').toBeEnabled();

    const gl = await webglReport(page);
    expect(
      gl.alive,
      `UI-20: the map must still be rendering after the interaction burst (${JSON.stringify(gl)})`,
    ).toBeGreaterThan(0);
    expect(gl.lost, `UI-20: no WebGL context may be lost by the interaction burst (${JSON.stringify(gl)})`).toBe(0);
    expect(
      runtimeErrors,
      `UI-20: interaction burst caused uncaught page errors:\n${runtimeErrors.join('\n')}`,
    ).toEqual([]);
  });
});

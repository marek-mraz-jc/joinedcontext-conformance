import { test, expect, openProject } from '../fixtures/portal.js';
import type { Page } from '@playwright/test';
import { BUDGET, report, startMeter } from '../fixtures/usability.js';

/**
 * Journey 33 (T-3282): five tasks from DEMO.md as a city employee gets them, done by a person new
 * to the platform with the controls on screen only: no typed address after the start page. Each
 * counts its clicks, page loads, views and seconds (`fixtures/usability.ts`), writes them to
 * `$JC_REPORTS_DIR/usability/<task>.json`, and is held to the 60-seconds roadmap's minute and ten
 * clicks; the slowest step of each is the friction filed from a run.
 *
 * Read-only on the demo project `helsinki`: nothing is proposed, so a monthly rerun leaves nothing
 * behind on dev. At 1440 px: the endpoint representations and the pipeline numbers are columns a
 * phone does not show.
 */
const PROJECT = process.env.PORTAL_USABILITY_PROJECT || 'helsinki';

test.use({ viewport: { width: 1440, height: 900 } });
test.setTimeout(180_000);

async function sidebar(page: Page, name: string): Promise<void> {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name, exact: true }).click();
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({ timeout: 30_000 });
}

/** Starts every task where a person starts: the Portal's first page, in English, in the project. */
async function begin(page: Page, task: string) {
  const meter = await startMeter(page, task);
  await page.goto('/?lang=en');
  await openProject(page, PROJECT);
  meter.step('in the project');
  return meter;
}

function heldToBudget(measure: { seconds: number; clicks: number; slowest: { step: string } | null }): void {
  const where = measure.slowest ? `; the slowest step: ${measure.slowest.step}` : '';
  expect(measure.seconds, `the task took longer than a minute${where}`).toBeLessThanOrEqual(BUDGET.seconds);
  expect(measure.clicks, `the task took more than ${BUDGET.clicks} clicks${where}`).toBeLessThanOrEqual(BUDGET.clicks);
}

test.describe('Journey 33: five real tasks, measured (T-3282)', () => {
  test('1. how many city-bike stations does Helsinki publish', async ({ steward }, info) => {
    const { page } = steward;
    const meter = await begin(page, '1-bike-stations-count');
    await sidebar(page, 'Context Spaces');
    meter.step('the spaces');
    await page.getByRole('table', { name: 'Context Spaces' }).getByRole('row').filter({ hasText: PROJECT }).first()
      .getByRole('link').first().click();
    const types = page.getByRole('table', { name: 'Entity types' });
    await expect(types).toBeVisible({ timeout: 30_000 });
    meter.step('inside the space');
    const count = types.getByRole('row').filter({ hasText: 'BikeHireDockingStation' }).getByRole('cell').nth(1);
    await expect(count, 'the number of stations is on the page').toHaveText(/^[1-9][\d,.\s]*$/, { timeout: 30_000 });
    meter.step('the count read');
    const measure = meter.done();
    await report(measure, info);
    heldToBudget(measure);
  });

  test("2. give a colleague today's events as a spreadsheet", async ({ steward }, info) => {
    const { page } = steward;
    const meter = await begin(page, '2-events-spreadsheet');
    await sidebar(page, 'Endpoints');
    meter.step('the endpoints');
    const row = page.getByRole('table', { name: 'Endpoints' }).getByRole('row').filter({ hasText: 'helsinki-events' }).first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    const sheet = row.getByRole('link', { name: 'csv', exact: true }).or(row.getByRole('link', { name: 'xlsx', exact: true })).first();
    await expect(sheet, 'the events endpoint offers a spreadsheet link').toBeVisible();
    meter.step('the spreadsheet link found');
    const href = await sheet.getAttribute('href');
    expect(href).toMatch(/\/api\/endpoint\/[^/]+\/file\.(csv|xlsx)$/);
    const answer = await page.request.get(href as string);
    expect(answer.status(), 'the link a colleague gets opens without a login').toBe(200);
    meter.step('the file answers');
    const measure = meter.done();
    await report(measure, info);
    heldToBudget(measure);
  });

  test('3. which bike-station details are hidden from the public', async ({ steward }, info) => {
    const { page } = steward;
    const meter = await begin(page, '3-hidden-from-public');
    await sidebar(page, 'Endpoints');
    meter.step('the endpoints');
    await page.getByRole('table', { name: 'Endpoints' }).getByRole('row').filter({ hasText: 'helsinki-bikes' }).first()
      .getByRole('link').first().click();
    await expect(page.getByRole('heading', { level: 2, name: 'What it answers' })).toBeVisible({ timeout: 30_000 });
    meter.step("the endpoint's page");
    const hidden = page.locator('dt', { hasText: 'Hidden attributes' }).locator('xpath=following-sibling::dd[1]');
    await expect(hidden, 'the page says what the public does not see').toBeVisible();
    await expect(hidden.getByRole('listitem').first().or(hidden.getByText('Every attribute the policy grants is published.'))).toBeVisible();
    meter.step('the hidden attributes read');
    const measure = meter.done();
    await report(measure, info);
    heldToBudget(measure);
  });

  test('4. show the bike stations on a map', async ({ steward }, info) => {
    const { page } = steward;
    const meter = await begin(page, '4-bikes-on-a-map');
    await sidebar(page, 'Explore data');
    meter.step('explore');
    const space = page.getByLabel('Context space');
    await expect(space.locator(`option[value="${PROJECT}"]`)).toBeAttached({ timeout: 30_000 });
    await space.selectOption(PROJECT);
    const type = page.locator('#explore-type');
    await expect(type.locator('option[value="BikeHireDockingStation"]')).toBeAttached({ timeout: 30_000 });
    await type.selectOption('BikeHireDockingStation');
    meter.step('space and type chosen');
    await page.getByRole('tab', { name: 'Map' }).click();
    await expect(page.getByRole('status').filter({ hasText: /^[1-9][\d,.\s]* of .* on the map/ })).toBeVisible({ timeout: 60_000 });
    meter.step('the stations on the map');
    const measure = meter.done();
    await report(measure, info);
    heldToBudget(measure);
  });

  test('5. are the bus positions still coming in', async ({ steward }, info) => {
    const { page } = steward;
    const meter = await begin(page, '5-bus-positions-live');
    await sidebar(page, 'Pipelines');
    meter.step('the pipelines');
    const row = page.getByRole('table', { name: 'Pipelines' }).getByRole('row').filter({ hasText: /vehicles/i }).first();
    await expect(row, 'the bus positions pipeline is listed').toBeVisible({ timeout: 30_000 });
    await expect(row.getByText(/^(Live|Deploying|Error|Not deployed|paused)$/).first(), 'its state is in words').toBeVisible();
    await expect(row.getByText('Writes nothing'), 'the pipeline writes').toHaveCount(0);
    meter.step('its state read');
    const measure = meter.done();
    await report(measure, info);
    heldToBudget(measure);
  });
});

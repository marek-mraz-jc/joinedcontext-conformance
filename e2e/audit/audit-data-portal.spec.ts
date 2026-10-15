/**
 * T-4161 — UX photo audit 5/7: the public open data portal (CKAN, data.dev.joinedcontext.com) as
 * an anonymous visitor. The home page; dataset search with no query, a query, one value of each
 * facet and no results; up to twelve datasets, each with its page and the preview of the resource
 * a visitor would open first, and that resource's download answering; the organizations, the
 * groups, `/about` and the API's front door; and `/sk/`, the Slovak the theme would offer.
 *
 * Datasets, facets and resources are read from the portal's own API at run time, so the audit
 * follows what is published. Every state at 375, 768 and 1440 px, saved by `shot` (shoot.ts)
 * under `$SHOTS_DIR`, which then gets its contact sheet. Read-only: nothing is posted, the
 * downloads are only asked for their status.
 *
 *   SHOTS_DIR=/workspace/evidence/<agent>/T-4161 npx playwright test -c e2e/playwright.config.ts audit-data-portal
 */
import { readdirSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { shot, writeIndex } from './shoot.js';
import type { Shot } from './shoot.js';

const PORTAL = (process.env.DATA_PORTAL_URL || 'https://data.dev.joinedcontext.com').replace(/\/$/, '');
const WIDTHS = [375, 768, 1440];
const AREA = 'data-portal';
const DIR = process.env.SHOTS_DIR;
// The theme offers the languages of `branding.languages.offered`; on dev that is English only,
// so `sk` is photographed once per width as the page a Slovak visitor gets.
const LANG = 'en';
// The Banská Bystrica datasets first, then the newest of the rest.
const FIRST_ORGS = ['banskabystrica', 'bbsk'];
const DATASETS = 12;

interface Resource {
  id: string;
  format: string;
  url: string;
  datastore_active?: boolean;
}
interface Dataset {
  name: string;
  organization?: { name: string };
  resources: Resource[];
}

const missed: string[] = [];
/** Set by any 429 the page's own requests get, frames and assets included. */
let limited = false;
/** When each request to the portal left this browser, for the pacing in `pace`. */
const sent: number[] = [];

/**
 * The edge allows one address 300 requests a minute on the catalogue host, assets included
 * (class 1). The audit loads pages faster than a person, so before each page it waits until the
 * last minute holds fewer than 200 of its requests, leaving a page's worth of room.
 */
async function pace(page: Page): Promise<void> {
  for (;;) {
    const now = Date.now();
    while (sent.length && sent[0] < now - 61_000) sent.shift();
    if (sent.length < 200) return;
    await page.waitForTimeout(sent[0] + 61_000 - now);
  }
}
const downloads: string[] = [];

async function api<T>(request: APIRequestContext, action: string, params: Record<string, string>): Promise<T> {
  const response = await request.get(`${PORTAL}/api/3/action/${action}?${new URLSearchParams(params)}`);
  expect(response.ok(), `${action} answers`).toBeTruthy();
  return ((await response.json()) as { result: T }).result;
}

/** The resource a visitor opens first: one with a table preview, then a CSV, then any. */
function firstResource(dataset: Dataset): Resource | undefined {
  const { resources } = dataset;
  return resources.find((r) => r.datastore_active) ?? resources.find((r) => r.format === 'CSV') ?? resources[0];
}

async function state(page: Page, what: Omit<Shot, 'width' | 'area' | 'who' | 'lang'> & { lang?: string }, address: string): Promise<void> {
  try {
    await pace(page);
    limited = false;
    let response = await page.goto(`${PORTAL}${address}`, { waitUntil: 'load' });
    // A 429 waits out the edge's window; a 5xx (dev restarting during an apply) gets the same wait.
    for (let attempt = 0; response && (response.status() === 429 || response.status() >= 500) && attempt < 3; attempt++) {
      await page.waitForTimeout((Number(response.headers()['retry-after'] ?? 60) + 1) * 1000);
      response = await page.goto(`${PORTAL}${address}`, { waitUntil: 'load' });
    }
    if (!response) throw new Error('no response');
    if (response.status() === 429 || response.status() >= 500) throw new Error(`answered ${response.status()}`);
    // A preview is an iframe that loads after the page; give it and the fonts a beat.
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => undefined);
    await page.waitForTimeout(800);
    // A page whose assets ran into the limit is not the page; wait the window out and retake it.
    for (let attempt = 0; limited && attempt < 3; attempt++) {
      limited = false;
      await page.waitForTimeout(61_000);
      await page.reload({ waitUntil: 'networkidle' });
    }
    if (limited) throw new Error('assets still rate limited');
    // A blank page is no picture of the portal: say so instead of saving white.
    if (!(await page.locator('body').innerText()).trim()) throw new Error('the page is blank');
    await shot(page, { area: AREA, who: 'anonymous', lang: LANG, ...what });
  } catch (error) {
    missed.push(`${address} ${what.state} ${page.viewportSize()?.width}: ${String(error).split('\n')[0]}`);
  }
}

test.describe.configure({ mode: 'serial' });
// Paced under the edge limit, the walk takes about 40 minutes.
test.setTimeout(3_600_000);

test('the open data portal, anonymous', async ({ browser, request }) => {
  test.skip(!DIR, 'SHOTS_DIR is not set');

  const facets = await api<{ search_facets: Record<string, { items: { name: string; count: number }[] }> }>(
    request,
    'package_search',
    { rows: '0', 'facet.field': JSON.stringify(['organization', 'tags', 'res_format', 'license_id']) },
  );
  const facetLinks = Object.entries(facets.search_facets)
    .filter(([, facet]) => facet.items.length)
    .map(([field, facet]) => {
      const top = [...facet.items].sort((a, b) => b.count - a.count)[0];
      return { field, address: `/dataset/?${new URLSearchParams({ [field]: top.name })}` };
    });
  expect(facetLinks.length, 'the search page has facets').toBeGreaterThan(0);

  const all = await api<{ results: Dataset[] }>(request, 'package_search', { rows: '100', sort: 'metadata_modified desc' });
  const datasets = [
    ...all.results.filter((d) => FIRST_ORGS.includes(d.organization?.name ?? '')),
    ...all.results.filter((d) => !FIRST_ORGS.includes(d.organization?.name ?? '')),
  ].slice(0, DATASETS);
  expect(datasets.length, 'the portal publishes datasets').toBeGreaterThanOrEqual(10);
  const organizations = await api<string[]>(request, 'organization_list', {});
  const groups = await api<string[]>(request, 'group_list', {});

  // Downloads are asked for once, not per width: the status a visitor's click would get.
  for (const dataset of datasets) {
    const resource = firstResource(dataset);
    if (!resource) {
      downloads.push(`${dataset.name}: no resource`);
      continue;
    }
    const answer = await request.get(resource.url, { maxRedirects: 5, timeout: 30_000 }).catch((e: Error) => e);
    const status = answer instanceof Error ? answer.message.split('\n')[0] : String(answer.status());
    downloads.push(`${dataset.name} ${resource.format} ${resource.url} -> ${status}`);
  }

  for (const width of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height: width < 768 ? 812 : 900 }, locale: 'sk-SK' });
    const page = await context.newPage();
    page.on('request', (asked) => {
      if (asked.url().startsWith(PORTAL)) sent.push(Date.now());
    });
    page.on('response', (answer) => {
      // The page's own 429 is retried by `state` before the load settles; everything else counts.
      if (answer.status() === 429 && answer.url() !== page.url()) limited = true;
    });
    try {
      await state(page, { route: '/', state: 'page' }, '/');
      await state(page, { route: '/sk/', state: 'slovak', lang: 'sk' }, '/sk/');
      await state(page, { route: '/dataset/', state: 'no-query' }, '/dataset/');
      await state(page, { route: '/dataset/', state: 'query-ovzdusie' }, '/dataset/?q=ovzdu%C5%A1ie');
      await state(page, { route: '/dataset/', state: 'query-air' }, '/dataset/?q=air');
      for (const facet of facetLinks) await state(page, { route: '/dataset/', state: `facet-${facet.field}` }, facet.address);
      await state(page, { route: '/dataset/', state: 'no-results' }, '/dataset/?q=qzxwvnothing');
      // A hyphen is Solr's NOT: this query matches everything without the words after it.
      await state(page, { route: '/dataset/', state: 'query-hyphenated' }, '/dataset/?q=qzxwv-nothing-is-called-this');
      await state(page, { route: '/organization/', state: 'page' }, '/organization/');
      for (const name of organizations) await state(page, { route: `/organization/${name}`, state: 'page' }, `/organization/${name}`);
      await state(page, { route: '/group/', state: 'page' }, '/group/');
      for (const name of groups) await state(page, { route: `/group/${name}`, state: 'page' }, `/group/${name}`);
      await state(page, { route: '/about', state: 'page' }, '/about');
      await state(page, { route: '/api/3', state: 'page' }, '/api/3');
      await state(page, { route: '/api/3/action/package_list', state: 'footer-link' }, '/api/3/action/package_list');
      for (const dataset of datasets) {
        await state(page, { route: `/dataset/${dataset.name}`, state: 'page' }, `/dataset/${dataset.name}`);
        const resource = firstResource(dataset);
        if (resource) {
          await state(
            page,
            { route: `/dataset/${dataset.name}/resource`, state: `preview-${resource.format}` },
            `/dataset/${dataset.name}/resource/${resource.id}`,
          );
        }
      }
    } finally {
      await context.close();
    }
  }
});

test.afterAll(() => {
  if (!DIR) return;
  const count = writeIndex(DIR);
  const shots = readdirSync(DIR).filter((file) => file.startsWith(`${AREA}-`) && file.endsWith('.png')).length;
  console.log(`UX photo audit, data portal: ${shots} screenshots in ${DIR} (${count} in the folder)`);
  console.log(`downloads (${downloads.length}):\n${downloads.join('\n')}`);
  if (missed.length) console.log(`not reached (${missed.length}):\n${missed.join('\n')}`);
  expect(missed, 'every state of the data portal is reached').toEqual([]);
  expect(shots).toBeGreaterThanOrEqual(80);
});

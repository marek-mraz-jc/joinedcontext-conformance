import type { Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test, expect, signIn, requireEnv } from '../fixtures/portal.js';

/**
 * Journey 41 (T-4867): rows into a space through "Import rows" and out again through the type's
 * export, as a steward does it from the menus. A CSV with three bad rows is refused row by row
 * with the reason; the fixed file is created; the rows are in the grid; a GeoJSON file of points
 * lands with its geometry; the export link the page offers gives back the same values, and the
 * space's GeoJSON representation the same points. A file over the limit is refused with its size
 * and the limit named. The viewer is refused the import in words and a write sent anyway is 403.
 *
 * Every row carries a run id and a fixed expiresAt one day ahead, and the journey deletes what it
 * created in `finally`, so the space ends as it began even when a step fails. Demo accounts only.
 * With `JC_EVIDENCE_DIR` set it keeps a screenshot per step at 1440 and 400 px and the round-trip
 * diff (`41-round-trip.json`) there.
 */
const PROJECT = process.env.JC_IMPORT_PROJECT || 'helsinki';
const SPACE = process.env.JC_IMPORT_SPACE || 'helsinki';
const TYPE = process.env.JC_IMPORT_TYPE || 'PointOfInterest';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RUN = `j41-${Date.now().toString(36)}`;
/** The Portal's import limit (importRows.ts MAX_BYTES), and a file just over it. */
const LIMIT_MB = 50;
const OVER_BYTES = 53_000_000;

test.setTimeout(240_000);

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: width > 600 ? 900 : 860 });
    await page.screenshot({ path: join(EVIDENCE, `41-${name}-${width}.png`) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

/** A request through the person's own session, as the page's transport sends it (CSRF header on writes). */
async function call(page: Page, method: string, path: string, body?: unknown, accept = 'application/json'): Promise<{ status: number; text: string }> {
  return page.evaluate(
    async ({ method, path, body, accept }) => {
      const csrf = document.cookie.split('; ').find((c) => c.startsWith('__Host-jc_csrf='))?.slice('__Host-jc_csrf='.length) ?? '';
      const headers: Record<string, string> = { Accept: accept };
      if (method !== 'GET') headers['x-csrf-token'] = csrf;
      if (body !== undefined) headers['content-type'] = 'application/json';
      const answer = await fetch(path, { method, credentials: 'same-origin', headers, body: body === undefined ? undefined : JSON.stringify(body) });
      return { status: answer.status, text: await answer.text() };
    },
    { method, path, body, accept },
  );
}

/** The ids of this run's entities in the space, read through the space surface. */
async function runIds(page: Page): Promise<string[]> {
  const params = new URLSearchParams({ type: TYPE, idPattern: `.*:${RUN}-.*`, pick: 'id', limit: '100' });
  const answer = await call(page, 'GET', `/cs/${SPACE}/ngsi-ld/v1/entities?${params}`);
  expect(answer.status, `listing the run's entities: ${answer.text.slice(0, 200)}`).toBe(200);
  return (JSON.parse(answer.text) as { id: string }[]).map((entity) => entity.id);
}

/** A CSV as the export writes it: a header, quoted cells with doubled quotes. */
function parseCsv(text: string): Record<string, string>[] {
  const records: string[][] = [];
  let record: string[] = [];
  let cell = '';
  let quoted = false;
  for (let at = 0; at < text.length; at += 1) {
    const char = text[at];
    if (quoted) {
      if (char === '"' && text[at + 1] === '"') (cell += '"'), (at += 1);
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') record.push(cell), (cell = '');
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[at + 1] === '\n') at += 1;
      record.push(cell), records.push(record), (record = []), (cell = '');
    } else cell += char;
  }
  if (cell !== '' || record.length > 0) record.push(cell), records.push(record);
  const [header = [], ...rows] = records.filter((r) => r.some((c) => c !== ''));
  return rows.map((row) => Object.fromEntries(header.map((name, i) => [name.replace(/^﻿/, ''), row[i] ?? ''])));
}

/** The rows the steward means to land: five places of the run, one with a comma, a quote and a diacritic. */
const FIELDS = ['name', 'serviceCategory', 'url'] as const;
const PLACES = [1, 2, 3, 4, 5].map((n) => ({
  id: `${RUN}-csv-${n}`,
  name: n === 3 ? 'Journey 41 "Oodi", Töölö' : `Journey 41 place ${n}`,
  serviceCategory: n % 2 ? 'library' : 'swimmingHall',
  url: `https://example.org/journey-41/${n}`,
}));
const quote = (text: string) => (/[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text);
const csvOf = (rows: Record<string, string>[]) =>
  [['id', ...FIELDS].join(','), ...rows.map((r) => [r.id, ...FIELDS.map((f) => r[f])].map(quote).join(','))].join('\n') + '\n';

/** A field's value in the export's flattened columns: `name`, `url.value` or `name.languageMap.@none`. */
function exportedValue(row: Record<string, string>, field: string): string | undefined {
  const columns = Object.keys(row).filter((c) => c === field || c === `${field}.value` || c.startsWith(`${field}.languageMap.`));
  return columns.map((c) => row[c]).find((value) => value !== '');
}

/** Three bad rows beside two good ones: a required field empty, an id that is no id, an id of another type. */
const BAD_CSV = csvOf([
  PLACES[0],
  { ...PLACES[1], serviceCategory: '' },
  { ...PLACES[2], id: 'not an id!' },
  { ...PLACES[3], id: `urn:ngsi-ld:Event:hel.fi:${SPACE}:${RUN}-x` },
  PLACES[4],
]);

const POINTS = [
  { id: `${RUN}-geo-1`, name: 'Journey 41 point 1', coordinates: [24.9384, 60.1739] },
  { id: `${RUN}-geo-2`, name: 'Journey 41 point 2', coordinates: [24.9525, 60.1699] },
  { id: `${RUN}-geo-3`, name: 'Journey 41 point 3', coordinates: [24.9156, 60.1873] },
];
const GEOJSON = JSON.stringify({
  type: 'FeatureCollection',
  features: POINTS.map((p) => ({
    type: 'Feature',
    id: p.id,
    properties: { name: p.name, serviceCategory: 'beach' },
    geometry: { type: 'Point', coordinates: p.coordinates },
  })),
});

async function openType(page: Page): Promise<void> {
  await page.goto(`/projects/${PROJECT}/spaces/${SPACE}?lang=en`);
  await page.getByRole('navigation', { name: 'Entity types' }).getByText(TYPE, { exact: true }).click();
  await expect(page.getByRole('button', { name: 'Import rows' })).toBeVisible({ timeout: 30_000 });
}

/** Opens "Import rows" and hands it a file; returns the dialog. */
async function upload(page: Page, name: string, mimeType: string, body: string | { path: string }) {
  const dialog = page.getByRole('dialog', { name: `Import rows into ${TYPE}` });
  if (!(await dialog.isVisible())) await page.getByRole('button', { name: 'Import rows' }).click();
  const back = dialog.getByRole('button', { name: 'Choose another file' });
  if (await back.isVisible()) await back.click();
  const input = dialog.locator('input[type=file]');
  // Playwright hands a buffer over 50 MB only as a file on disk.
  await (typeof body === 'string' ? input.setInputFiles({ name, mimeType, buffer: Buffer.from(body) }) : input.setInputFiles(body.path));
  return dialog;
}

/** One day ahead, so a row a failed cleanup leaves behind still goes away by itself. */
async function expireTomorrow(dialog: ReturnType<Page['getByRole']>): Promise<void> {
  await dialog.getByRole('radio', { name: 'At one fixed time' }).check();
}

test.describe('Journey 41: import and export round trip (T-4867)', () => {
  test('the steward imports CSV and GeoJSON, fixes the refused rows, and the export gives the same values back', async ({ steward }, info) => {
    const { page } = steward;
    const tooLarge = info.outputPath('too-large.csv');
    writeFileSync(tooLarge, Buffer.alloc(OVER_BYTES, 'a'));
    try {
      await openType(page);
      await shot(page, '01-type');

      // A file over the limit is refused before a byte is read, with its size and the limit named.
      let dialog = await upload(page, 'too-large.csv', 'text/csv', { path: tooLarge });
      // Soft, as is the GeoJSON field below: both need the Portal of T-4867, and the rest of the
      // round trip still runs and cleans up on an older Portal.
      await expect.soft(dialog.getByText(new RegExp(`The file is 50\\.5 MB; an import reads at most ${LIMIT_MB} MB\\.`))).toBeVisible();
      await shot(page, '02-too-large');

      // Three bad rows: each refused on its own line with the reason; the two good ones are ready.
      dialog = await upload(page, 'places-with-errors.csv', 'text/csv', BAD_CSV);
      await expireTomorrow(dialog);
      await expect(dialog.getByText('2 rows are ready, 3 refused.')).toBeVisible();
      const refused = dialog.getByRole('list', { name: 'Rows that will not be created' });
      await expect(refused.getByRole('listitem')).toHaveCount(3);
      await expect(refused.getByRole('listitem').nth(0)).toContainText(/Row 3.*serviceCategory is required/);
      await expect(refused.getByRole('listitem').nth(1)).toContainText(/Row 4.*cannot be the last part of an id/);
      await expect(refused.getByRole('listitem').nth(2)).toContainText(/Row 5.*names another type, organization or space/);
      await shot(page, '03-refused-rows');

      // The steward fixes the file and imports it again: all five created.
      dialog = await upload(page, 'places.csv', 'text/csv', csvOf(PLACES));
      await expireTomorrow(dialog);
      await expect(dialog.getByText('5 rows are ready, none refused.')).toBeVisible();
      await dialog.getByRole('button', { name: 'Create 5 rows' }).click();
      await expect(dialog.getByText('5 rows created, none refused.')).toBeVisible({ timeout: 60_000 });
      await shot(page, '04-created');
      await dialog.getByRole('button', { name: 'Close' }).first().click();

      // GeoJSON points land with their geometry in the type's location.
      dialog = await upload(page, 'points.geojson', 'application/geo+json', GEOJSON);
      await expect.soft(dialog.getByRole('combobox', { name: 'location' }), 'the geometry has a field to go to').toHaveValue('location');
      await expireTomorrow(dialog);
      await expect(dialog.getByText('3 rows are ready, none refused.')).toBeVisible();
      await shot(page, '05-geojson-mapped');
      await dialog.getByRole('button', { name: 'Create 3 rows' }).click();
      await expect(dialog.getByText('3 rows created, none refused.')).toBeVisible({ timeout: 60_000 });
      await dialog.getByRole('button', { name: 'Close' }).first().click();

      // The rows are in the space: the grid opens one by its address.
      const ids = await runIds(page);
      expect(ids, 'every imported row is an entity').toHaveLength(PLACES.length + POINTS.length);
      const first = ids.find((id) => id.endsWith(`:${RUN}-csv-3`)) as string;
      await page.goto(`/projects/${PROJECT}/spaces/${SPACE}?lang=en&type=${TYPE}&row=${encodeURIComponent(first)}`);
      const details = page.getByRole('dialog', { name: /^Details: / });
      await expect(details).toBeVisible({ timeout: 30_000 });
      await expect(details).toContainText('Journey 41 "Oodi", Töölö');
      await shot(page, '06-row-in-grid');
      await page.keyboard.press('Escape');

      // Export: the link the page offers, read as the person's browser would follow it.
      const link = page.locator('a[download]').filter({ hasText: 'CSV' }).first();
      await expect(link).toBeVisible();
      const href = (await link.getAttribute('href')) as string;
      const exported = await call(page, 'GET', href, undefined, 'text/csv');
      expect(exported.status, `the export answers: ${exported.text.slice(0, 200)}`).toBe(200);
      const rows = parseCsv(exported.text);
      const idColumn = Object.keys(rows[0] ?? {}).find((name) => name === 'id' || name === '@id') ?? 'id';
      const diff = PLACES.map((place) => {
        const row = rows.find((r) => (r[idColumn] ?? '').endsWith(`:${place.id}`));
        const lost = FIELDS.filter((field) => (row ? exportedValue(row, field) : undefined) !== place[field]);
        return { id: place.id, found: Boolean(row), lost, exported: row ?? null, imported: place };
      });

      // GeoJSON: the space's own representation of the points (NGSI-LD geo+json, ETSI GS CIM 009 §6.3.15).
      const params = new URLSearchParams({ type: TYPE, idPattern: `.*:${RUN}-geo-.*`, limit: '100' });
      const geo = await call(page, 'GET', `/cs/${SPACE}/ngsi-ld/v1/entities?${params}`, undefined, 'application/geo+json');
      expect(geo.status, `the GeoJSON read answers: ${geo.text.slice(0, 200)}`).toBe(200);
      const features = (JSON.parse(geo.text) as { features?: { id: string; geometry?: { coordinates?: number[] } }[] }).features ?? [];
      const geoDiff = POINTS.map((point) => {
        const feature = features.find((f) => f.id.endsWith(`:${point.id}`));
        return { id: point.id, found: Boolean(feature), imported: point.coordinates, exported: feature?.geometry?.coordinates ?? null };
      });
      if (EVIDENCE) writeFileSync(join(EVIDENCE, '41-round-trip.json'), JSON.stringify({ run: RUN, export: href, csv: diff, geojson: geoDiff }, null, 2));
      expect(diff.filter((d) => !d.found || d.lost.length > 0), 'the CSV export gives back every value imported').toEqual([]);
      expect(geoDiff.filter((d) => JSON.stringify(d.exported) !== JSON.stringify(d.imported)), 'every point comes back where it was').toEqual([]);
    } finally {
      const ids = await runIds(page).catch(() => [] as string[]);
      if (ids.length > 0) {
        const removed = await call(page, 'POST', `/cs/${SPACE}/ngsi-ld/v1/entityOperations/delete`, ids);
        expect(removed.status, `cleanup removes the run's ${ids.length} entities: ${removed.text.slice(0, 200)}`).toBeLessThan(300);
      }
    }
  });

  test('the viewer is refused the import in words, and a write sent anyway is 403 with nothing written', async ({ browser, baseURL }) => {
    const context = await browser.newContext({ baseURL: baseURL ?? requireEnv('BASE_URL'), ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    try {
      await page.goto('/');
      await signIn(page, requireEnv('PORTAL_VIEWER_USER'), requireEnv('PORTAL_VIEWER_PASSWORD'), baseURL);
      await openType(page);
      const button = page.getByRole('button', { name: 'Import rows' });
      await expect(button).toHaveAttribute('aria-disabled', 'true');
      await expect(button, 'the refusal says what the role lacks').toHaveAccessibleDescription(/your role does not permit/);
      await shot(page, '07-viewer-refused');

      const id = `urn:ngsi-ld:${TYPE}:hel.fi:${SPACE}:${RUN}-viewer`;
      const written = await call(page, 'POST', `/cs/${SPACE}/ngsi-ld/v1/entityOperations/create`, [
        { id, type: TYPE, name: { type: 'Property', value: 'viewer probe' }, serviceCategory: { type: 'Property', value: 'library' } },
      ]);
      expect(written.status).toBe(403);
      expect(JSON.parse(written.text)).toMatchObject({ title: 'Access Denied by Policy' });
      expect((await call(page, 'GET', `/cs/${SPACE}/ngsi-ld/v1/entities/${encodeURIComponent(id)}`)).status, 'nothing was written').toBe(404);
    } finally {
      await context.close();
    }
  });
});

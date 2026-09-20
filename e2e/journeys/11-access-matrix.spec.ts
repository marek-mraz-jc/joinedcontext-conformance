import type { APIRequestContext, Page } from '@playwright/test';
import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 11 (T-0356): Project → Data Access renders what the caller may do, and every cell of
 * that table is proved against the endpoint that actually serves. TS-12 (the view is opened by
 * its real navigation), EP-46, EP-52, EP-60, AC-05.
 *
 * The matrix is the claim and the endpoint is the evidence. The view reads the grant document
 * from the endpoint's own `/access` surface with the browser's credentials, so the evidence
 * calls carry exactly the same credentials: the signed-in context's own request object when the
 * view names a subject, an anonymous request when the view says "anonymous caller". Comparing a
 * claim made for one caller against an answer given to another would prove nothing.
 *
 * Nothing here widens a grant. The only write the spec attempts is one the matrix says is
 * refused; a 201 there is the failure, not a side effect the spec has to clean up.
 */
const access = /^access$|^prístup$/i;
const ANONYMOUS = /anonymous caller|anonymný volajúci/i;
const SUBJECT_LINE = / in space | v priestore /i;
const NONE = /^(none|žiadne)$/i;
const ALL = /^(all|všetky)$/i;

/** Keys of an NGSI-LD entity that are not attributes, so no projection can hide them. */
const STRUCTURAL = new Set(['id', 'type', 'scope', 'createdAt', 'modifiedAt', 'deletedAt']);

/** One row of the rendered matrix: an entity type and what the caller may do with it. */
interface Cell {
  type: string;
  reads: string[];
  writes: string[];
  attributes: string[] | '*';
}

/** What the endpoint answered for one cell. `write` is null when no write was attempted. */
interface Observed {
  read: number;
  write: number | null;
  entity: Record<string, unknown> | null;
}

function where(subject: string, cell: Cell, action: string, attribute: string): string {
  return `subject ${subject} · entity type ${cell.type} · action ${action} · attribute ${attribute}`;
}

/**
 * Every disagreement between one claimed cell and what the endpoint answered, each naming the
 * subject, the action and the attribute. Pure on purpose: the live test feeds it observations,
 * and the last test in this file feeds it a flipped claim to prove a lie is actually caught.
 */
export function disagreements(subject: string, cell: Cell, seen: Observed): string[] {
  const problems: string[] = [];
  const read = cell.reads[0] ?? 'retrieveEntity';

  if (cell.reads.length > 0 && seen.read !== 200) {
    problems.push(
      `${where(subject, cell, read, 'all')}: the matrix grants this read, ` +
        `the endpoint answered ${seen.read}`,
    );
  }
  if (cell.writes.length === 0 && seen.write !== null && seen.write !== 403) {
    problems.push(
      `${where(subject, cell, 'createEntity', 'all')}: the matrix grants no write, ` +
        `the endpoint answered ${seen.write} instead of 403`,
    );
  }
  if (cell.attributes !== '*' && seen.entity) {
    for (const name of Object.keys(seen.entity)) {
      if (STRUCTURAL.has(name) || name.startsWith('@')) {
        continue;
      }
      if (!cell.attributes.includes(name)) {
        problems.push(
          `${where(subject, cell, read, name)}: the matrix projects only ` +
            `[${cell.attributes.join(', ')}], the endpoint returned this attribute`,
        );
      }
    }
  }
  return problems;
}

/** An explicitly denied cell: the named attributes must not come back at all (EP-59). */
export function stillReturned(
  subject: string,
  cell: Cell,
  entity: Record<string, unknown> | null,
): string[] {
  if (!entity || cell.attributes === '*') {
    return [];
  }
  return cell.attributes
    .filter((name) => name in entity)
    .map(
      (name) =>
        `${where(subject, cell, 'retrieveEntity', name)}: the matrix denies this attribute, ` +
        'the endpoint returned it',
    );
}

/** The texts of a cell that holds either a list of chips or the word "none" / "all". */
async function chips(cell: import('@playwright/test').Locator): Promise<string[]> {
  const items = cell.locator('li');
  if ((await items.count()) > 0) {
    return (await items.allInnerTexts()).map((text) => text.trim()).filter(Boolean);
  }
  const text = (await cell.innerText()).trim();
  return NONE.test(text) ? [] : [text];
}

/** Reads one rendered grant table; the header row has no row header and is skipped. */
async function readTable(page: Page, caption: RegExp): Promise<Cell[]> {
  const table = page.getByRole('table', { name: caption });
  if ((await table.count()) === 0) {
    return [];
  }
  const cells: Cell[] = [];
  for (const row of await table.first().getByRole('row').all()) {
    const header = row.getByRole('rowheader');
    if ((await header.count()) === 0) {
      continue;
    }
    const columns = row.getByRole('cell');
    const attributes = (await columns.nth(2).innerText()).trim();
    cells.push({
      type: (await header.innerText()).split('\n')[0].trim(),
      reads: await chips(columns.nth(0)),
      writes: await chips(columns.nth(1)),
      attributes: ALL.test(attributes) ? '*' : await chips(columns.nth(2)),
    });
  }
  return cells;
}

/**
 * Opens Data Access, reads the matrix, and answers with the endpoint slug, the subject the view
 * names and the cells it renders. The endpoint comes from the view's own select, so the slug the
 * evidence calls use is the one the matrix was computed for.
 */
async function readMatrix(page: Page): Promise<{
  slug: string;
  subject: string;
  space: string;
  anonymous: boolean;
  permissions: Cell[];
  prohibitions: Cell[];
}> {
  await openView(page, access);

  const select = page.getByLabel(/^endpoint$/i);
  await expect(
    select,
    'Project → Access must offer the endpoint whose access document it shows',
  ).toBeVisible({ timeout: 30_000 });

  const wanted = process.env.PORTAL_ENDPOINT || 'public-air';
  const labels = await select.locator('option').allInnerTexts();
  const match = labels.find((label) => label.toLowerCase().includes(wanted.toLowerCase()));
  if (match) {
    await select.selectOption({ label: match });
  }
  const slug = (await select.inputValue()).trim();
  expect(slug, `the endpoint select must carry a published slug; options: ${labels.join(', ')}`)
    .toMatch(/^[a-z2-7]{26}$/);

  const line = page.getByText(SUBJECT_LINE).first();
  await expect(
    line,
    'the matrix must say whose access it describes before any cell can be proved',
  ).toBeVisible({ timeout: 30_000 });
  const text = (await line.innerText()).trim();
  const space = text.split(SUBJECT_LINE)[1]?.trim() ?? '';

  return {
    slug,
    subject: text.split(SUBJECT_LINE)[0]?.trim() || 'anonymous caller',
    space,
    anonymous: ANONYMOUS.test(text),
    permissions: await readTable(page, /^(data access|prístup k údajom)$/i),
    prohibitions: await readTable(page, /^(explicitly denied|výslovne zakázané)$/i),
  };
}

/** Asks the endpoint what it really does with one cell, with the caller's own credentials. */
async function observe(
  api: APIRequestContext,
  base: string,
  space: string,
  cell: Cell,
): Promise<Observed> {
  const list = await api.get(
    `${base}/ngsi-ld/v1/entities?type=${encodeURIComponent(cell.type)}&limit=1`,
    { headers: { accept: 'application/json' }, failOnStatusCode: false },
  );
  const read = list.status();
  const body: unknown = read === 200 ? await list.json().catch(() => null) : null;
  const entity =
    Array.isArray(body) && typeof body[0] === 'object' && body[0] !== null
      ? (body[0] as Record<string, unknown>)
      : null;

  // Only a write the matrix says is refused is ever attempted: a 201 here is the defect.
  let write: number | null = null;
  if (cell.writes.length === 0) {
    const probe = await api.post(`${base}/ngsi-ld/v1/entities`, {
      headers: { 'content-type': 'application/json' },
      data: {
        id: `urn:ngsi-ld:${cell.type}:banskabystrica.sk:${space}:e2e-t0356-probe`,
        type: cell.type,
      },
      failOnStatusCode: false,
    });
    write = probe.status();
  }
  return { read, write, entity };
}

/** The whole comparison for one signed-in browser session. */
async function proveMatrix(
  page: Page,
  session: APIRequestContext,
  anonymousApi: APIRequestContext,
  baseURL: string,
): Promise<number> {
  const matrix = await readMatrix(page);
  // EP-60: the view reads the grant with the browser's credentials, so the evidence uses them
  // too. When the view names no subject, the document is the anonymous one and so is the proof.
  const api = matrix.anonymous ? anonymousApi : session;
  const base = `${baseURL}/api/endpoint/${matrix.slug}`;

  const problems: string[] = [];
  for (const cell of matrix.permissions) {
    problems.push(...disagreements(matrix.subject, cell, await observe(api, base, matrix.space, cell)));
  }
  for (const cell of matrix.prohibitions) {
    const seen = await observe(api, base, matrix.space, cell);
    problems.push(...stillReturned(matrix.subject, cell, seen.entity));
  }

  expect(
    problems,
    `the Data Access matrix and the endpoint disagree:\n  ${problems.join('\n  ')}`,
  ).toEqual([]);
  return matrix.permissions.length;
}

test.describe('Journey 11: the Data Access matrix agrees with the endpoint (T-0356)', () => {
  test('EP-60 every cell the steward is shown is what the endpoint answers', async ({
    steward,
    request,
    baseURL,
  }) => {
    const rows = await proveMatrix(steward.page, steward.context.request, request, baseURL as string);
    expect(
      rows,
      'DEMO seeds a public read grant, so the matrix must describe at least one entity type; ' +
        'an empty table means the endpoint has no policy, not that the spec has nothing to do',
    ).toBeGreaterThan(0);
  });

  test('EP-52 every cell demo.viewer is shown is what the endpoint answers them', async ({
    browser,
    request,
    baseURL,
  }) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');

    const { signIn } = await import('../fixtures/portal.js');
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string);
    try {
      await proveMatrix(page, context.request, request, baseURL as string);
    } finally {
      await context.close();
    }
  });

  /**
   * The flipped grant is a flipped *claim*, not a policy change on the cluster: widening a real
   * grant to watch a test go red would leave the demo endpoint open if the run died in between
   * (T-0356 Security). The comparison is the same function the two tests above run.
   */
  test('a cell the endpoint does not honour is reported by subject, action and attribute', () => {
    const flipped: Cell = {
      type: 'AirQualityObserved',
      reads: ['retrieveEntity'],
      writes: [],
      attributes: ['pm10'],
    };
    const honest: Observed = {
      read: 200,
      write: 403,
      entity: { id: 'urn:ngsi-ld:AirQualityObserved:x', type: 'AirQualityObserved', pm10: 1, no2: 2 },
    };

    const problems = disagreements('demo.viewer', flipped, honest);
    expect(problems, 'an attribute outside the projection must be reported').toHaveLength(1);
    expect(problems[0]).toContain('demo.viewer');
    expect(problems[0]).toContain('AirQualityObserved');
    expect(problems[0]).toContain('retrieveEntity');
    expect(problems[0]).toContain('no2');

    // The same claim against an endpoint that honours it produces nothing at all.
    expect(disagreements('demo.viewer', { ...flipped, attributes: '*' }, honest)).toEqual([]);

    // A write the matrix says is refused but the endpoint accepts is named as well.
    expect(
      disagreements('demo.viewer', { ...flipped, attributes: '*' }, { ...honest, write: 201 })[0],
    ).toMatch(/createEntity.*201 instead of 403/s);

    // An explicit denial that still comes back names the attribute it failed to hide.
    expect(
      stillReturned('demo.viewer', { ...flipped, attributes: ['no2'] }, honest.entity)[0],
    ).toMatch(/no2/);
  });
});

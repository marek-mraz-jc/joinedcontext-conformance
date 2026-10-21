import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Browser, Page } from '@playwright/test';
import { test, expect } from '../fixtures/portal.js';

/**
 * Journey 41 (T-4868, UI-61, CC-76..CC-80, PF-83): a steward works on a copy of the helsinki
 * project through the menus: changes the air-quality pipeline and its data source in the copy,
 * starts the copy's preview, reads Compare, brings the copy back as one proposal, and the
 * project changes only once an approver approves it. A second copy is discarded and leaves
 * nothing behind. A viewer is refused every write in words.
 *
 * The edits are the titles of the two resources: a title is a change a person can read back on
 * the page, and it restarts no stream. Afterwards the journey proposes the old titles and
 * approves them, so dev is left as it was.
 *
 * Accounts: PORTAL_STEWARD_USER/PASSWORD (demo.steward), PORTAL_APPROVER_USER/PASSWORD
 * (demo.approver), PORTAL_VIEWER_USER/PASSWORD (demo.viewer). With `JC_EVIDENCE_DIR` set it
 * keeps 1440 px and 400 px screenshots of each step there.
 */
const PROJECT = process.env.JC_COPY_PROJECT ?? 'helsinki';
const PIPELINE = 'air-quality';
const SOURCE = 'fmi-air-quality';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RUN = Date.now().toString(36).slice(-6);
const COPY = `t4868-${RUN}`;
const THROWAWAY = `t4868-d-${RUN}`;
const SUFFIX = `, tried in copy ${RUN}`;

type Manifest = { apiVersion: string; kind: string; metadata: { name: string; title?: Record<string, string> }; spec: unknown };

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: join(EVIDENCE, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function signedIn(browser: Browser, baseURL: string | undefined, signIn: (p: Page, u: string, s: string, b?: string) => Promise<void>, who: string): Promise<Page> {
  const user = process.env[`PORTAL_${who}_USER`];
  const pass = process.env[`PORTAL_${who}_PASSWORD`];
  test.skip(!user || !pass, `PORTAL_${who}_USER and PORTAL_${who}_PASSWORD name the demo account`);
  const context = await browser.newContext({ baseURL, locale: 'en-GB', viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto('/');
  await signIn(page, user as string, pass as string, baseURL);
  return page;
}

/** One API call on the page's own session, with its CSRF token, as the Portal's code sends it. */
async function call(page: Page, method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown }> {
  return page.evaluate(async ([m, p, b]) => {
    const csrf = (document.cookie.match(/__Host-jc_csrf=([^;]+)/) ?? [])[1] ?? '';
    const answer = await fetch(p as string, {
      method: m as string,
      headers: { 'content-type': 'application/json', 'x-csrf-token': csrf },
      body: b === undefined ? undefined : JSON.stringify(b),
    });
    const text = await answer.text();
    let json: unknown = text;
    try { json = JSON.parse(text); } catch { /* a text answer stays text */ }
    return { status: answer.status, json };
  }, [method, path, body] as const);
}

async function manifest(page: Page, plural: string, name: string): Promise<Manifest> {
  const answer = await call(page, 'GET', `/api/v1/projects/${PROJECT}/${plural}/${name}`);
  expect(answer.status, `${plural}/${name} reads`).toBe(200);
  const { status: _status, ...rest } = answer.json as Manifest & { status?: unknown };
  return rest;
}

const titleOf = (doc: Manifest) => doc.metadata.title?.en ?? '';

/** The copy's bar: the proof that a page is in the copy and not in the project. */
function bar(page: Page) {
  return page.getByRole('region', { name: 'Copy' });
}

async function sidebar(page: Page, name: string): Promise<void> {
  await page.getByRole('navigation').getByRole('link', { name, exact: true }).first().click();
}

/** Opens a resource from its list in the sidebar, edits its title in the form and proposes it into the copy. */
async function retitle(page: Page, menu: string, plural: string, name: string, title: string): Promise<void> {
  await sidebar(page, menu);
  await expect(page).toHaveURL(new RegExp(`/${plural}(\\?|$)`), { timeout: 20_000 });
  await expect(bar(page), `${menu} stays inside the copy`).toBeVisible();
  await page.getByRole('link', { name, exact: true }).first().click();
  await page.getByRole('button', { name: 'Edit', exact: true }).first().click();
  await expect(bar(page), `the ${plural} editor stays inside the copy`).toBeVisible({ timeout: 20_000 });
  const field = page.getByRole('textbox', { name: 'Title', exact: true }).first();
  await expect(field).toBeVisible({ timeout: 30_000 });
  await field.fill(title);
  await page.getByRole('button', { name: 'Check', exact: true }).last().click();
  const propose = page.getByRole('button', { name: /^Propose/ }).last();
  await expect(propose).not.toHaveAttribute('aria-disabled', 'true', { timeout: 90_000 });
  await shot(page, `${plural}-edited`);
  const written = page.waitForResponse(
    (answer) => answer.request().method() === 'PUT' && answer.url().includes(`/${plural}/${name}?`) && answer.url().includes(`workspace=${COPY}`) && !answer.url().includes('dryRun'),
    { timeout: 60_000 },
  );
  await propose.click();
  const answer = await written;
  expect(answer.status(), `the ${plural} change lands in the copy, not as a Change of the project`).toBe(200);
  expect(((await answer.json()) as { workspace?: string }).workspace).toBe(COPY);
}

/** Proposes `doc` to the project and approves it, so the journey leaves dev as it found it. */
async function restore(steward: Page, approver: Page, plural: string, doc: Manifest): Promise<void> {
  const path = `/api/v1/projects/${PROJECT}/${plural}/${doc.metadata.name}`;
  const now = await manifest(steward, plural, doc.metadata.name);
  if (titleOf(now) === titleOf(doc)) return;
  expect((await call(steward, 'PUT', `${path}?dryRun=All`, doc)).status).toBe(200);
  const proposed = await call(steward, 'PUT', path, doc);
  expect(proposed.status, JSON.stringify(proposed.json)).toBe(202);
  const change = (proposed.json as { metadata?: { name?: string } }).metadata?.name as string;
  await expect(async () => {
    expect((await call(approver, 'POST', `/api/v1/projects/${PROJECT}/changes/${change}/approve`, {})).status).toBe(202);
  }).toPass({ timeout: 90_000, intervals: [5_000] });
}

test.describe('Journey 41: work on a copy, try it, bring it back (T-4868)', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(1_200_000);

  test('a steward changes two resources in a copy and brings them back through approval', async ({ browser, baseURL, signIn }) => {
    const steward = await signedIn(browser, baseURL, signIn, 'STEWARD');
    const approver = await signedIn(browser, baseURL, signIn, 'APPROVER');
    const before = { pipeline: await manifest(steward, 'pipelines', PIPELINE), source: await manifest(steward, 'datasources', SOURCE) };
    const wanted = { pipeline: titleOf(before.pipeline) + SUFFIX, source: titleOf(before.source) + SUFFIX };
    try {
      // 1. Copies, from the menu; a copy of the whole project.
      await steward.goto(`/projects/${PROJECT}/spaces?lang=en`);
      await sidebar(steward, 'Copies');
      await expect(steward.getByRole('heading', { name: 'Copies', level: 1 })).toBeVisible({ timeout: 30_000 });
      await shot(steward, 'copies-list');
      await steward.getByRole('button', { name: 'Work on a copy' }).first().click();
      const dialog = steward.getByRole('dialog', { name: 'Work on a copy' });
      await dialog.getByRole('textbox', { name: 'Name' }).fill(COPY);
      await dialog.getByRole('textbox', { name: 'Title' }).fill(`Air quality titles ${RUN}`);
      await shot(steward, 'copy-dialog');
      await dialog.getByRole('button', { name: 'Start the copy' }).click();
      const row = steward.getByRole('row').filter({ hasText: COPY });
      await expect(row).toBeVisible({ timeout: 30_000 });

      // 2. Into the copy: the space's rows are live, so the copy offers no entity write there.
      await row.getByRole('button', { name: 'Open' }).click();
      await expect(bar(steward)).toBeVisible({ timeout: 20_000 });
      await steward.getByRole('link', { name: PROJECT, exact: true }).first().click().catch(() => undefined);
      await expect(steward.getByText('Entities stay live in a copy')).toBeVisible({ timeout: 30_000 });
      await shot(steward, 'copy-space-read-only');

      // 3. Two resources changed in the copy, through their own forms.
      await retitle(steward, 'Pipelines', 'pipelines', PIPELINE, wanted.pipeline);
      await retitle(steward, 'Data sources', 'datasources', SOURCE, wanted.source);
      expect(titleOf(await manifest(steward, 'pipelines', PIPELINE)), 'nothing reaches the project from a copy').toBe(titleOf(before.pipeline));

      // 4. Try it: the copy's preview, with its pipelines paused.
      await bar(steward).getByRole('link', { name: 'Try it' }).click();
      await expect(steward.getByRole('heading', { name: 'Try the copy' })).toBeVisible({ timeout: 30_000 });
      await steward.getByRole('button', { name: 'Start the preview' }).click();
      await expect(steward.getByTestId('preview-state')).toHaveText(/Running|Did not start/, { timeout: 300_000 });
      await shot(steward, 'try-it');
      await expect(steward.getByTestId('preview-state'), 'the preview starts').toHaveText('Running');
      await expect(steward.getByText(PIPELINE).first(), 'the copy\'s pipeline is paused in the preview').toBeVisible();

      // 5. Compare: exactly the two files, each with its new title in words.
      // Try it shows no copy bar: the way on is the trail back to the copies and the row's Compare.
      await steward.getByRole('link', { name: 'Copies', exact: true }).first().click();
      await steward.getByRole('row').filter({ hasText: COPY }).getByRole('link', { name: 'Compare' }).click();
      await expect(steward.getByRole('heading', { name: `What the copy ${COPY} changes` })).toBeVisible({ timeout: 30_000 });
      await expect(steward.getByText('0 added, 2 changed, 0 removed')).toBeVisible({ timeout: 30_000 });
      const files = steward.getByRole('listitem').filter({ has: steward.getByText('Changed', { exact: true }) });
      await expect(files).toHaveCount(2);
      for (const summary of await steward.getByText('Fields', { exact: true }).all()) await summary.click();
      await expect(steward.getByText(wanted.pipeline).first()).toBeVisible();
      await expect(steward.getByText(wanted.source).first()).toBeVisible();
      await shot(steward, 'compare');

      // 6. Bring back as one proposal; the project is unchanged until it is approved.
      await steward.getByRole('link', { name: 'Bring back' }).first().click();
      await expect(steward.getByRole('heading', { name: 'Bring the copy back' })).toBeVisible({ timeout: 30_000 });
      const proposed = steward.waitForResponse((answer) => answer.request().method() === 'POST' && answer.url().endsWith(`/workspaces/${COPY}/propose`), { timeout: 60_000 });
      await steward.getByRole('button', { name: 'Propose as one change' }).click();
      const proposal = await proposed;
      expect(proposal.status()).toBe(202);
      const change = ((await proposal.json()) as { metadata?: { name?: string } }).metadata?.name as string;
      expect(change).toMatch(/^chg-/);
      await expect(steward.getByText('Proposed as')).toBeVisible();
      await shot(steward, 'bring-back-proposed');
      expect(titleOf(await manifest(steward, 'pipelines', PIPELINE)), 'the project waits for the approval').toBe(titleOf(before.pipeline));

      // 7. The approver approves it on the Approvals page; then the project has both titles.
      await approver.goto(`/projects/${PROJECT}/spaces?lang=en`);
      await sidebar(approver, 'Approvals');
      await approver.goto(`/projects/${PROJECT}/approvals/${change}?lang=en`);
      await expect(approver.getByText(PIPELINE).first()).toBeVisible({ timeout: 30_000 });
      await shot(approver, 'approval');
      await approver.getByRole('button', { name: /^Approve/ }).first().click();
      await expect.poll(async () => titleOf(await manifest(steward, 'pipelines', PIPELINE)), { timeout: 300_000, intervals: [10_000] }).toBe(wanted.pipeline);
      expect(titleOf(await manifest(steward, 'datasources', SOURCE))).toBe(wanted.source);
      await steward.goto(`/projects/${PROJECT}/pipelines/${PIPELINE}?lang=en`);
      await expect(steward.getByRole('heading', { name: wanted.pipeline })).toBeVisible({ timeout: 30_000 });
      await shot(steward, 'project-updated');
    } finally {
      await call(steward, 'DELETE', `/api/v1/projects/${PROJECT}/workspaces/${COPY}`);
      await restore(steward, approver, 'pipelines', before.pipeline);
      await restore(steward, approver, 'datasources', before.source);
    }
    await steward.context().close();
    await approver.context().close();
  });

  test('a discarded copy leaves nothing behind, and a viewer is refused every write in words', async ({ browser, baseURL, signIn }) => {
    const steward = await signedIn(browser, baseURL, signIn, 'STEWARD');
    const viewer = await signedIn(browser, baseURL, signIn, 'VIEWER');
    const opened = await call(steward, 'POST', `/api/v1/projects/${PROJECT}/workspaces`, { name: THROWAWAY, ttlDays: 1, scope: { kind: 'project' } });
    expect(opened.status, JSON.stringify(opened.json)).toBe(201);
    try {
      // The viewer reads the copies and opens none, and may not touch the steward's.
      await viewer.goto(`/projects/${PROJECT}/spaces?lang=en`);
      await sidebar(viewer, 'Copies');
      await expect(viewer.getByRole('heading', { name: 'Copies', level: 1 })).toBeVisible({ timeout: 30_000 });
      const start = viewer.getByRole('button', { name: 'Work on a copy' }).first();
      await expect(start).toHaveAttribute('aria-disabled', 'true');
      await start.hover();
      await expect(viewer.getByText('Your role may not propose anything in this project, so it cannot open a copy.').first()).toBeVisible();
      const theirs = viewer.getByRole('row').filter({ hasText: THROWAWAY });
      await expect(theirs).toBeVisible();
      await expect(theirs.getByRole('button', { name: 'Discard' })).toHaveCount(0);
      await shot(viewer, 'viewer-copies');
      expect((await call(viewer, 'POST', `/api/v1/projects/${PROJECT}/workspaces`, { name: `${THROWAWAY}-v`, ttlDays: 1, scope: { kind: 'project' } })).status).toBe(403);
      expect((await call(viewer, 'DELETE', `/api/v1/projects/${PROJECT}/workspaces/${THROWAWAY}`)).status).toBe(403);
      expect((await call(viewer, 'POST', `/api/v1/projects/${PROJECT}/workspaces/${THROWAWAY}/propose`)).status).toBe(403);
      const write = await call(viewer, 'PUT', `/api/v1/projects/${PROJECT}/pipelines/${PIPELINE}?workspace=${THROWAWAY}`, await manifest(viewer, 'pipelines', PIPELINE));
      expect(write.status, 'a viewer writes nothing into a copy').toBe(403);
      await theirs.getByRole('link').first().click();
      await expect(bar(viewer).getByText(/Read only/)).toBeVisible({ timeout: 20_000 });
      await shot(viewer, 'viewer-in-copy');

      // The steward discards it from the list, after a question that names it.
      await steward.goto(`/projects/${PROJECT}/spaces?lang=en`);
      await sidebar(steward, 'Copies');
      const mine = steward.getByRole('row').filter({ hasText: THROWAWAY });
      await mine.getByRole('button', { name: 'Discard' }).click();
      const ask = steward.getByRole('alertdialog').or(steward.getByRole('dialog'));
      await expect(ask).toContainText(`Discard the copy ${THROWAWAY}?`);
      await shot(steward, 'discard-confirm');
      await ask.getByRole('button', { name: 'Discard' }).click();
      await expect(mine).toHaveCount(0, { timeout: 30_000 });
      await shot(steward, 'discarded');
      expect((await call(steward, 'GET', `/api/v1/projects/${PROJECT}/workspaces/${THROWAWAY}`)).status, 'the record is gone').toBe(404);
      expect((await call(steward, 'GET', `/api/v1/projects/${PROJECT}/workspaces/${THROWAWAY}/compare`)).status, 'and its branch').toBe(404);
    } finally {
      await call(steward, 'DELETE', `/api/v1/projects/${PROJECT}/workspaces/${THROWAWAY}`);
    }
    await steward.context().close();
    await viewer.context().close();
  });
});

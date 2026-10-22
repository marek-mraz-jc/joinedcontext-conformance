import { randomBytes } from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Browser, BrowserContext, Page } from '@playwright/test';
import { test, expect, requireEnv, signIn } from '../fixtures/portal.js';

/**
 * Journey 47 (T-5098, DM-124..DM-126, API/01 §46): a person's data, exported and erased through the
 * Portal section "A person's data" (space Retention tab, T-4870), live on dev.
 *
 * The steward proposes a sandbox space, a model whose class `Rider` names the slot `rider` as its
 * `dataSubjectRef` and marks `riderName` `personalData`, the space pointed at the model with a day of
 * history for `Rider`, an Endpoint and a Policy that lets the steward write and read it; the approver
 * approves each. The steward writes one Rider and updates its name, so the history holds two versions.
 * Then, in the Portal: the export downloads a file holding the Rider and both versions; the erasure
 * shows its preview, is confirmed and reaches done. Through the Endpoint, the personal attributes are
 * gone from the current state and from the history while `zone` stays; Activity lists "erased a data
 * subject" with the reason. demo.viewer finds both buttons off, and the same POSTs sent directly
 * answer 403. Everything is removed in afterAll; the space is a sandbox with a one-day TTL besides.
 *
 * Accounts: PORTAL_USER/PASSWORD (steward), PORTAL_APPROVER_USER/PASSWORD, PORTAL_VIEWER_USER/PASSWORD.
 * JC_PERSONAL_PROJECT (helsinki), JC_PERSONAL_ORG (hel.fi). With `JC_EVIDENCE_DIR` set it keeps a
 * 1440 px and a 400 px screenshot of each step there.
 */
const PROJECT = process.env.JC_PERSONAL_PROJECT ?? 'helsinki';
const ORG = process.env.JC_PERSONAL_ORG ?? 'hel.fi';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RUN = Date.now().toString(36).slice(-6);
const NAME = `t5098-${RUN}`;
const MODEL = `t5098-model-${RUN}`;
const LEFTOVER = /^t5098-(model-)?[0-9a-z]{6}(-write)?$/;
const API_VERSION = 'joinedcontext.com/v1alpha1';
const SUBJECT = `rider-${RUN}`;
const REASON = `Journey 47: Art. 15 and 17 request ${RUN}`;
const ENTITY = `urn:ngsi-ld:Rider:${NAME}-1`;
const CORE = 'https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.8.jsonld';
const SPACE_PATH = `/api/v1/projects/${PROJECT}/spaces/${NAME}`;

const LINKML = `id: https://example.org/${MODEL}
name: ${MODEL}
prefixes:
  linkml: https://w3id.org/linkml/
default_range: string
imports: [linkml:types, ngsi-ld-core]
slots:
  rider:
    range: string
  riderName:
    range: string
    annotations:
      personalData: true
  zone:
    range: string
classes:
  Rider:
    is_a: Entity
    annotations:
      dataSubjectRef: rider
    slots: [rider, riderName, zone]
`;

type Change = { metadata: { name: string }; status?: { phase?: string } };
type Doc = { plural: string; doc: Record<string, unknown> };

/** 32 characters of lowercase base32 (160 bits), the Endpoint slug EP-02 asks for. */
function slug(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567';
  return [...randomBytes(32)].map((byte) => alphabet[byte % 32]).join('');
}
const SLUG = slug();
const ENDPOINT = `/api/endpoint/${SLUG}/ngsi-ld/v1`;

const SPACE: Record<string, unknown> = {
  apiVersion: API_VERSION, kind: 'ContextSpace',
  metadata: { name: NAME, namespace: PROJECT, title: { en: `Journey 47 personal data ${RUN}` } },
  spec: { isSandbox: true, ttlDays: 1, defaultLocale: 'en' },
};
const POINTED: Record<string, unknown> = {
  ...SPACE,
  spec: {
    ...(SPACE.spec as object),
    dataModelRef: { kind: 'DataModel', name: MODEL },
    retention: [{ entityType: 'Rider', history: 'P1D', record: 'every' }],
  },
};
/** What the steward proposes after the space and its model, in order; removed in reverse. */
const manifests = (): Doc[] => [
  {
    plural: 'endpoints',
    doc: {
      apiVersion: API_VERSION, kind: 'Endpoint',
      metadata: { name: NAME, namespace: PROJECT, title: { en: `Journey 47 personal data ${RUN}` } },
      spec: { contextSpaceRef: NAME, slug: SLUG, audience: 'organization', enabledRepresentations: ['ngsi-ld'] },
    },
  },
  {
    plural: 'policies',
    doc: {
      apiVersion: API_VERSION, kind: 'Policy',
      metadata: { name: `${NAME}-write`, namespace: PROJECT, title: { en: `Journey 47: the steward writes ${RUN}` } },
      spec: {
        contextSpaceRef: { kind: 'ContextSpace', name: NAME }, assigner: `did:web:${ORG}`,
        assignee: { kind: 'user', id: requireEnv('PORTAL_USER') },
        operations: ['createEntity', 'updateAttrs', 'retrieveOps', 'queryTemporal', 'retrieveTemporal'],
      },
    },
  },
];

async function csrfHeaders(context: BrowserContext, type = 'application/json'): Promise<Record<string, string>> {
  const csrf = (await context.cookies()).find((cookie) => cookie.name === '__Host-jc_csrf');
  expect(csrf, 'the session carries a CSRF cookie').toBeTruthy();
  return { 'x-csrf-token': csrf?.value ?? '', 'content-type': type };
}

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: width > 600 ? 900 : 860 });
    await page.screenshot({ path: join(EVIDENCE, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

/**
 * The approver approves a pending change; a red-lane change repeats the resource's name. A kind the
 * approver's role cannot decide (a DataModel's deletion) is the sandbox's own: its steward decides it.
 */
async function approve(approver: BrowserContext, change: Change, confirm: string, steward?: BrowserContext): Promise<void> {
  if (change.status?.phase !== 'PendingApproval') return;
  const decide = async (who: BrowserContext) =>
    who.request.post(`/api/v1/projects/${PROJECT}/changes/${change.metadata.name}/approve`, {
      headers: await csrfHeaders(who),
      data: { confirm },
      timeout: 60_000,
    });
  let response = await decide(approver);
  if (response.status() === 403 && steward) response = await decide(steward);
  expect(response.status(), `CC-34: ${change.metadata.name} is approved: ${await response.text()}`).toBe(202);
}

/** Waits until the change is merged and `plural/name` answers, with `ready` holding on its body. */
async function landed(steward: BrowserContext, change: Change, plural: string, name: string, ready: (body: Record<string, any>) => boolean = () => true): Promise<void> {
  await expect(async () => {
    const answer = await steward.request.get(`/api/v1/projects/${PROJECT}/changes/${change.metadata.name}`);
    const phase = ((await answer.json()) as Change).status?.phase;
    expect(phase, `${plural}/${name}: change ${change.metadata.name} was rejected`).not.toBe('Rejected');
    expect(['Merged', 'Applied'], `${plural}/${name}: change ${change.metadata.name} is merged`).toContain(phase);
    const resource = await steward.request.get(`/api/v1/projects/${PROJECT}/${plural}/${name}`);
    expect(resource.status(), `${plural}/${name} exists`).toBe(200);
    expect(ready((await resource.json()) as Record<string, any>), `${plural}/${name} is as proposed`).toBe(true);
  }).toPass({ timeout: 600_000, intervals: [5_000] });
}

/** PF-57: the manifest's own check, green, then the proposal (POST a new one, PUT a change). */
async function propose(steward: BrowserContext, plural: string, doc: Record<string, unknown>, method: 'post' | 'put' = 'post'): Promise<Change> {
  const name = (doc.metadata as { name: string }).name;
  const path = `/api/v1/projects/${PROJECT}/${plural}${method === 'put' ? `/${name}` : ''}`;
  const check = await steward.request[method](`${path}?dryRun=All`, { headers: await csrfHeaders(steward), data: doc, timeout: 60_000 });
  const checked = await check.text();
  expect(check.status(), `${plural}/${name} is checked: ${checked}`).toBe(200);
  expect((JSON.parse(checked) as { verdict?: { ok?: boolean } }).verdict?.ok, `${plural}/${name} passes its check: ${checked}`).toBe(true);
  const proposal = await steward.request[method](path, { headers: await csrfHeaders(steward), data: doc, timeout: 60_000 });
  const proposed = await proposal.text();
  expect(proposal.status(), `${plural}/${name} is proposed: ${proposed}`).toBe(202);
  return JSON.parse(proposed) as Change;
}

/**
 * A session of the person PORTAL_<who>_USER names, kept for the whole journey: the dev edge allows
 * 300 requests a minute per address, and a sign-in it refused leaves a blank page and no CSRF cookie.
 * One that ends without the cookie waits out the edge's minute and tries again.
 */
async function session(browser: Browser, baseURL: string | undefined, who: string): Promise<BrowserContext> {
  for (let attempt = 0; ; attempt += 1) {
    const context = await browser.newContext({ baseURL, locale: 'en-GB', ignoreHTTPSErrors: true });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, requireEnv(`PORTAL${who}_USER`), requireEnv(`PORTAL${who}_PASSWORD`), baseURL);
    // The Portal sets the CSRF cookie on the UI's first API call, a few seconds after the sign-in lands.
    const csrf = async () => (await context.cookies()).some((cookie) => cookie.name === '__Host-jc_csrf');
    for (const until = Date.now() + 30_000; Date.now() < until && !(await csrf()); ) await page.waitForTimeout(1_000);
    if (await csrf()) return context;
    await context.close();
    if (attempt === 2) throw new Error(`PORTAL${who}_USER signed in three times without a session cookie: the edge refuses sign-ins`);
    await new Promise((resolve) => setTimeout(resolve, 65_000));
  }
}

/** Deletes `plural/name` and waits until it is gone; '' when it is, the reason otherwise. */
async function remove(steward: BrowserContext, approver: BrowserContext, plural: string, name: string): Promise<string> {
  try {
    const response = await steward.request.delete(`/api/v1/projects/${PROJECT}/${plural}/${name}?confirm=${name}`, {
      headers: await csrfHeaders(steward),
      timeout: 60_000,
    });
    if (response.status() === 404) return '';
    if (response.status() !== 202) return `delete ${plural}/${name}: ${response.status()} ${await response.text()}`;
    await approve(approver, (await response.json()) as Change, name, steward);
    await expect(async () => {
      const gone = await steward.request.get(`/api/v1/projects/${PROJECT}/${plural}/${name}`);
      expect(gone.status(), `${plural}/${name} is removed`).toBe(404);
    }).toPass({ timeout: 600_000, intervals: [10_000] });
    return '';
  } catch (error) {
    return `delete ${plural}/${name}: ${error instanceof Error ? error.message.split('\n')[0] : String(error)}`;
  }
}

/** The Rider through the Endpoint, as the steward reads it. */
async function rider(steward: BrowserContext): Promise<Record<string, unknown> | null> {
  const answer = await steward.request.get(`${ENDPOINT}/entities/${encodeURIComponent(ENTITY)}`, { headers: { accept: 'application/json' } });
  return answer.status() === 200 ? ((await answer.json()) as Record<string, unknown>) : null;
}

/** The Rider's stored history through the Endpoint: its instances per attribute. */
async function history(steward: BrowserContext): Promise<Record<string, unknown[]>> {
  const answer = await steward.request.get(`${ENDPOINT}/temporal/entities/${encodeURIComponent(ENTITY)}?lastN=100`, {
    headers: { accept: 'application/json' },
  });
  expect(answer.status(), `the steward reads the Rider's history: ${(await answer.text()).slice(0, 300)}`).toBe(200);
  const body = (await answer.json()) as Record<string, unknown>;
  const instances: Record<string, unknown[]> = {};
  for (const [key, value] of Object.entries(body)) {
    if (['id', 'type', '@context'].includes(key)) continue;
    instances[key] = Array.isArray(value) ? value : [value];
  }
  return instances;
}

/** The space's Retention tab, scrolled to "A person's data". */
async function openSection(page: Page): Promise<void> {
  await page.goto(`/projects/${PROJECT}/spaces/${NAME}?tab=retention&lang=en`);
  const heading = page.getByRole('heading', { name: "A person's data" });
  await expect(heading, 'the Retention tab holds the section T-4870 added').toBeVisible({ timeout: 30_000 });
  await heading.scrollIntoViewIfNeeded();
}

test.describe("Journey 47: a person's data exported and erased from current state and history (T-5098)", () => {
  test.describe.configure({ mode: 'serial', timeout: 1_800_000 });

  let proposed = false;
  let written = false;
  let erased = false;

  /** The steward's and the approver's sessions, one sign-in each for the whole journey. */
  let steward: { context: BrowserContext; page: Page };
  let approver: { context: BrowserContext };

  test.beforeAll(async ({ browser, baseURL }) => {
    test.setTimeout(1_800_000);
    const context = await session(browser, baseURL, '');
    steward = { context, page: context.pages()[0] };
    approver = { context: await session(browser, baseURL, '_APPROVER') };
    // A run that died before its cleanup left its resources in the demo project: remove them first,
    // consumers before what they consume.
    for (const plural of ['policies', 'endpoints', 'spaces', 'datamodels']) {
      const listed = await steward.context.request.get(`/api/v1/projects/${PROJECT}/${plural}`);
      const items = listed.ok() ? (((await listed.json()) as { items?: { metadata?: { name?: string } }[] }).items ?? []) : [];
      for (const name of items.map((item) => item.metadata?.name ?? '').filter((name) => LEFTOVER.test(name))) {
        await remove(steward.context, approver.context, plural, name);
      }
    }
  });

  test.afterAll(async () => {
    test.setTimeout(1_800_000);
    const failures: string[] = [];
    if (proposed) {
      for (const { plural, doc } of manifests().reverse()) {
        failures.push(await remove(steward.context, approver.context, plural, (doc.metadata as { name: string }).name));
      }
      failures.push(await remove(steward.context, approver.context, 'spaces', NAME));
      failures.push(await remove(steward.context, approver.context, 'datamodels', MODEL));
    }
    await steward?.context.close();
    await approver?.context.close();
    expect(failures.filter(Boolean), 'the scratch resources are all removed').toEqual([]);
  });

  test('the steward proposes the space, its personal-data model, an Endpoint and a Policy; the approver approves', async () => {
    const space = await propose(steward.context, 'spaces', SPACE);
    proposed = true;
    await approve(approver.context, space, NAME);
    await landed(steward.context, space, 'spaces', NAME);

    // The model lives in the space: its source is checked, then proposed.
    const source = `/api/v1/projects/${PROJECT}/datamodels/${MODEL}/source?space=${NAME}`;
    const yaml = await csrfHeaders(steward.context, 'text/yaml');
    const checked = await steward.context.request.put(`${source}&dryRun=All`, { headers: yaml, data: LINKML });
    expect(checked.ok(), `the model's dry run: ${await checked.text()}`).toBe(true);
    const put = await steward.context.request.put(source, { headers: yaml, data: LINKML, timeout: 60_000 });
    expect(put.status(), `the model is proposed: ${await put.text()}`).toBe(202);
    const model = (await put.json()) as Change;
    await approve(approver.context, model, MODEL, steward.context);
    await landed(steward.context, model, 'datamodels', MODEL);

    const pointed = await propose(steward.context, 'spaces', POINTED, 'put');
    await approve(approver.context, pointed, NAME);
    await landed(steward.context, pointed, 'spaces', NAME, (body) => body.spec?.dataModelRef?.name === MODEL);

    for (const { plural, doc } of manifests()) {
      const name = (doc.metadata as { name: string }).name;
      const change = await propose(steward.context, plural, doc);
      await approve(approver.context, change, name);
      await landed(steward.context, change, plural, name);
    }
  });

  test('the steward writes one Rider and updates its name: the history holds two versions', async () => {
    test.skip(!proposed, 'the scratch space was not proposed');
    const headers = { ...(await csrfHeaders(steward.context, 'application/ld+json')) };
    const first = new Date(Date.now() - 60_000).toISOString();
    // The Endpoint and its Policy reach the gateway a little after the change is applied.
    await expect(async () => {
      const created = await steward.context.request.post(`${ENDPOINT}/entities`, {
        headers,
        data: {
          id: ENTITY, type: 'Rider', '@context': CORE,
          rider: { type: 'Property', value: SUBJECT, observedAt: first },
          riderName: { type: 'Property', value: `Aino ${RUN}`, observedAt: first },
          zone: { type: 'Property', value: 'Kallio', observedAt: first },
        },
      });
      expect([201, 409], `the steward creates the Rider: ${created.status()} ${(await created.text()).slice(0, 300)}`).toContain(created.status());
    }).toPass({ timeout: 600_000, intervals: [10_000] });

    // CIM 009 5.6.4 partial attribute update, the operation `updateAttrs` grants on the broker's view
    // (chyby.md: the gateway's PATCH …/attrs reading of updateAttrs answers 404 there).
    const updated = await steward.context.request.patch(`${ENDPOINT}/entities/${encodeURIComponent(ENTITY)}/attrs/riderName`, {
      headers,
      data: { '@context': CORE, type: 'Property', value: `Aino Virtanen ${RUN}`, observedAt: new Date().toISOString() },
    });
    expect(updated.status(), `the steward updates the name: ${await updated.text()}`).toBe(204);
    await expect(async () => {
      expect((await history(steward.context)).riderName?.length, 'two versions of riderName are stored').toBe(2);
    }).toPass({ timeout: 120_000, intervals: [5_000] });
    // The Portal finds a subject through the space's own surface (/cs/{space}), whose view the
    // reconciler rewrites after the Policy lands: the dry run counts what an erasure would take.
    const started = Date.now();
    await expect(async () => {
      const dry = await steward.context.request.post(`${SPACE_PATH}/erasures?dryRun=All`, {
        headers: await csrfHeaders(steward.context),
        data: { subject: SUBJECT, reason: REASON },
      });
      const space = await steward.context.request.get(SPACE_PATH);
      const conditions = JSON.stringify(((await space.json()) as { status?: { conditions?: unknown } }).status?.conditions ?? null).slice(0, 600);
      expect(((await dry.json()) as { entities?: number }).entities, `the dry run finds the Rider; space conditions: ${conditions}`).toBe(1);
    }).toPass({ timeout: 900_000, intervals: [20_000] });
    console.log(`the space surface found the Rider after ${Math.round((Date.now() - started) / 1000)} s`);
    written = true;
  });

  test("the steward exports the person's data and erases it in the Portal", async () => {
    test.skip(!written, 'the Rider was not written');
    const { page } = steward;
    await openSection(page);
    await shot(page, '47-01-section');

    await page.getByRole('button', { name: "Export the person's data" }).click();
    await expect(page.getByText("Enter the person's identifier.")).toBeVisible();
    await shot(page, '47-02-required');

    await page.getByLabel("Person's identifier").fill(SUBJECT);
    await page.getByLabel('Reason').fill(REASON);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: "Export the person's data" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(new RegExp(`^personal-data-${NAME}-\\d{4}-\\d{2}-\\d{2}\\.json$`));
    const exported = JSON.parse(readFileSync(await file.path(), 'utf8')) as {
      entities: { class: string; entity: { id: string }; history: Record<string, unknown> }[];
    };
    expect(exported.entities.map((e) => e.entity.id), 'DM-124: the file holds the Rider').toEqual([ENTITY]);
    const versions = exported.entities[0].history.riderName;
    expect(Array.isArray(versions) ? versions.length : 0, 'DM-124: the file holds both versions of the name').toBe(2);
    expect(JSON.stringify(exported)).toContain(`Aino ${RUN}`);
    await expect(page.getByText(/Exported 1 record/)).toBeVisible();
    await shot(page, '47-03-exported');

    await page.getByRole('button', { name: "Erase the person's data…" }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog.getByText('Rider: the personal attributes (rider, riderName) of 1 entity')).toBeVisible({ timeout: 30_000 });
    await shot(page, '47-04-preview');

    await dialog.getByRole('button', { name: 'Erase' }).click();
    await expect(page.getByText(/Erasure er-\d{8}-[0-9a-f]{12} is done/), 'every store reports done').toBeVisible({ timeout: 300_000 });
    await expect(page.getByText(/Current values and history: done, 1 removed/)).toBeVisible();
    await expect(page.getByLabel("Person's identifier"), 'the value leaves the page').toHaveValue('');
    erased = true;
    await shot(page, '47-05-done');
  });

  test('the personal attributes are gone from current state and history, and Activity records the erasure', async () => {
    test.skip(!erased, 'the erasure did not finish');
    const now = await rider(steward.context);
    expect(now, 'a record without values stays').not.toBeNull();
    expect(Object.keys(now!), 'DM-125: no personal attribute in the current state').not.toContain('riderName');
    expect(Object.keys(now!)).not.toContain('rider');
    expect((now!.zone as { value?: string }).value, 'what is not personal stays').toBe('Kallio');
    const kept = await history(steward.context);
    expect(kept.riderName ?? [], 'DM-125: no version of the name in the history').toEqual([]);
    expect(kept.rider ?? [], 'DM-125: no version of the subject in the history').toEqual([]);
    expect(JSON.stringify(kept)).not.toContain(`Aino`);

    const again = await steward.context.request.post(`${SPACE_PATH}/subjects/export`, {
      headers: await csrfHeaders(steward.context),
      data: { subject: SUBJECT, reason: REASON },
    });
    expect(again.status(), await again.text()).toBe(200);
    expect(((await again.json()) as { entities: unknown[] }).entities, 'nothing names the person any more').toEqual([]);

    const activity = await steward.context.request.get(`/api/v1/projects/${PROJECT}/activity?space=${NAME}&kind=personal-data&limit=20`);
    expect(activity.status()).toBe(200);
    const events = JSON.stringify(await activity.json());
    expect(events, 'the erasure is recorded').toContain('erased a data subject');
    expect(events, 'with its reason').toContain(REASON);
    expect(events, 'never with the subject itself').not.toContain(SUBJECT);
    const { page } = steward;
    await page.goto(`/projects/${PROJECT}/activity?lang=en`);
    await expect(page.getByText(/erased a data subject/).first(), 'Activity shows the erasure').toBeVisible({ timeout: 30_000 });
    await shot(page, '47-06-activity');
  });

  test("demo.viewer finds both buttons off and is refused the same requests", async ({ browser, baseURL }) => {
    test.skip(!proposed, 'the scratch space was not proposed');
    const viewer = await session(browser, baseURL, '_VIEWER');
    const page = await viewer.newPage();
    try {
      await openSection(page);
      await expect(page.getByRole('button', { name: "Export the person's data" })).toHaveAttribute('aria-disabled', 'true');
      await expect(page.getByRole('button', { name: "Erase the person's data…" })).toHaveAttribute('aria-disabled', 'true');
      await shot(page, '47-07-viewer');
      for (const route of ['subjects/export', 'erasures', 'erasures?dryRun=All']) {
        const refused = await viewer.request.post(`${SPACE_PATH}/${route}`, {
          headers: await csrfHeaders(viewer),
          data: { subject: SUBJECT, reason: REASON },
        });
        expect(refused.status(), `a viewer's ${route}: ${await refused.text()}`).toBe(403);
      }
    } finally {
      await viewer.close();
    }
  });
});

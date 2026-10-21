import { randomBytes } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Browser, BrowserContext, Page } from '@playwright/test';
import { test, expect, requireEnv, signIn } from '../fixtures/portal.js';

/**
 * Journey 45 (T-4843, PL-80..PL-83, DM-130, NGSI-LD 4.22 expiresAt): a pipeline's `spec.lifetime`
 * ends what it wrote, live. The steward proposes a scratch space on the project's model, an
 * Endpoint into it, the write and read Policies and a pipeline without a source that writes three
 * entities a minute; the approver approves each. The lifetime gives
 *   - Vehicle `rolling-*`: the whole entity, rolling PT2M; the pipeline stops writing it at the cutoff,
 *   - Vehicle `steady-*`: written every minute, its attribute `location` rolling PT2M, which the
 *     pipeline stops writing at the cutoff while the entity goes on,
 *   - Alert `fixed-*`: the whole entity at a fixed date three minutes after the cutoff.
 * Each one disappears from the Endpoint within one sweep after its end, and nothing else does:
 * `steady-*` stays with its `speed`. Afterwards the journey deletes the pipeline, the Policies, the
 * Endpoint and the space (whose deletion takes its entities); the space is a sandbox with a
 * one-day TTL, so a run that dies before its cleanup still leaves nothing for long.
 *
 * The cutoff is JC_LIFETIME_DEPLOY_MIN minutes (default 10) after the start: the time the four
 * approvals and the reconciler get to put the pipeline on the runner. A dev in a rollout can take
 * longer, and the journey then says so instead of judging the lifetimes.
 *
 * Accounts: PORTAL_USER/PASSWORD (a steward of the project), PORTAL_APPROVER_USER/PASSWORD (a
 * second person). JC_LIFETIME_PROJECT (helsinki), JC_LIFETIME_MODEL (helsinki, a model of that
 * project with the classes Vehicle and Alert), JC_LIFETIME_ORG (hel.fi), JC_LIFETIME_GROUP
 * (helsinki, the group the read Policy names). With `JC_EVIDENCE_DIR` set it keeps 1440 px and
 * 400 px screenshots of the space and the pipeline there.
 */
const PROJECT = process.env.JC_LIFETIME_PROJECT ?? 'helsinki';
const MODEL = process.env.JC_LIFETIME_MODEL ?? 'helsinki';
const ORG = process.env.JC_LIFETIME_ORG ?? 'hel.fi';
const GROUP = process.env.JC_LIFETIME_GROUP ?? 'helsinki';
const DEPLOY_MS = Number(process.env.JC_LIFETIME_DEPLOY_MIN ?? '10') * 60_000;
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RUN = Date.now().toString(36).slice(-6);
const NAME = `t4843-${RUN}`;
const API_VERSION = 'joinedcontext.com/v1alpha1';
const MINUTE = 60_000;
/** The broker's sweep runs once a minute (DM-83); a read may land just before it, and polls every 15 s. */
const SWEEP_MS = 90_000;
const POLL_MS = 15_000;

const START = Date.now();
const CUTOFF = Math.ceil((START + DEPLOY_MS) / 1000) * 1000;
const FIXED = new Date(CUTOFF + 3 * MINUTE).toISOString().replace(/\.\d{3}Z$/, 'Z');

type Change = { metadata: { name: string }; status?: { phase?: string } };
type Attribute = { value?: unknown; expiresAt?: string };
type Entity = { id: string; type: string; expiresAt?: string } & Record<string, unknown>;

/** 32 characters of lowercase base32 (160 bits), the Endpoint slug EP-02 asks for. */
function slug(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567';
  return [...randomBytes(32)].map((byte) => alphabet[byte % 32]).join('');
}

const SLUG = slug();
// The ids end in these; the space segment between is the reconciler's JC_SPACE (PF-84).
const ROLLING = `:rolling-${RUN}`;
const STEADY = `:steady-${RUN}`;
const ALERT = `:fixed-${RUN}`;

/** The pipeline's mapping: three entities a minute, the first and the location of the second only before the cutoff. */
const MAPPING = `let urn = "urn:ngsi-ld:%s:%s:%s:%s-${RUN}"
let domain = env("JC_ORG_DOMAIN")
let space = env("JC_SPACE")
let open = timestamp_unix() < ${CUTOFF / 1000}
let at = now()
let place = {"type": "GeoProperty", "value": {"type": "Point", "coordinates": [24.94, 60.17]}, "observedAt": $at}
let speed = {"type": "Property", "value": 1, "unitCode": "MTS", "observedAt": $at}
let all = [
  {"id": $urn.format("Vehicle", $domain, $space, "rolling"), "type": "Vehicle", "speed": $speed, "location": $place},
  {"id": $urn.format("Vehicle", $domain, $space, "steady"), "type": "Vehicle", "speed": $speed, "location": if $open { $place } else { deleted() }},
  {"id": $urn.format("Alert", $domain, $space, "fixed"), "type": "Alert",
   "category": {"type": "Property", "value": "traffic"}, "alertSource": {"type": "Property", "value": "Journey 45"},
   "name": {"type": "LanguageProperty", "languageMap": {"en": "Journey 45: a fixed-date lifetime"}}}
]
root = $all.filter(e -> $open || !e.id.has_suffix(":rolling-${RUN}"))
`;

const MANIFESTS: Array<{ plural: string; doc: Record<string, unknown> }> = [
  {
    plural: 'spaces',
    doc: {
      apiVersion: API_VERSION, kind: 'ContextSpace',
      metadata: { name: NAME, namespace: PROJECT, title: { en: `Journey 45 lifetimes ${RUN}` } },
      spec: {
        isSandbox: true, ttlDays: 1, defaultLocale: 'en',
        dataModelRef: { kind: 'DataModel', name: MODEL },
        retention: [{ entityType: 'Vehicle', history: 'off' }, { entityType: 'Alert', history: 'off' }],
      },
    },
  },
  {
    plural: 'endpoints',
    doc: {
      apiVersion: API_VERSION, kind: 'Endpoint',
      metadata: { name: NAME, namespace: PROJECT, title: { en: `Journey 45 lifetimes ${RUN}` } },
      spec: { contextSpaceRef: NAME, slug: SLUG, audience: 'organization', enabledRepresentations: ['ngsi-ld'] },
    },
  },
  {
    plural: 'policies',
    doc: {
      apiVersion: API_VERSION, kind: 'Policy',
      metadata: { name: `${NAME}-write`, namespace: PROJECT, title: { en: `Journey 45: the pipelines write ${RUN}` } },
      spec: {
        contextSpaceRef: { kind: 'ContextSpace', name: NAME }, assigner: `did:web:${ORG}`,
        assignee: { kind: 'serviceAccount', id: 'pipelines' }, operations: ['upsertBatch', 'createBatch', 'queryBatch'],
      },
    },
  },
  {
    plural: 'policies',
    doc: {
      apiVersion: API_VERSION, kind: 'Policy',
      metadata: { name: `${NAME}-read`, namespace: PROJECT, title: { en: `Journey 45: the city's people read ${RUN}` } },
      spec: {
        contextSpaceRef: { kind: 'ContextSpace', name: NAME }, assigner: `did:web:${ORG}`,
        assignee: { kind: 'group', id: GROUP }, operations: ['retrieveOps'],
      },
    },
  },
  {
    plural: 'pipelines',
    doc: {
      apiVersion: API_VERSION, kind: 'Pipeline',
      metadata: { name: NAME, namespace: PROJECT, title: { en: `Journey 45 lifetimes ${RUN}` } },
      spec: {
        class: 'auto', period: '1m',
        compute: { kind: 'bloblang', bloblang: MAPPING },
        targetEndpoint: `urn:ngsi-ld:Endpoint:${ORG}:${PROJECT}:${NAME}`,
        quotas: { maxMemoryMb: 64, cpuMillicores: 50 },
        lifetime: [
          { type: 'Alert', entity: { at: FIXED } },
          { type: 'Vehicle', entity: { rolling: 'PT2M' }, attributes: [{ names: ['location'], rolling: 'PT2M' }] },
        ],
      },
    },
  },
];

async function csrfHeaders(context: BrowserContext): Promise<Record<string, string>> {
  const csrf = (await context.cookies()).find((cookie) => cookie.name === '__Host-jc_csrf');
  expect(csrf, 'the session carries a CSRF cookie').toBeTruthy();
  return { 'x-csrf-token': csrf?.value ?? '', 'content-type': 'application/json' };
}

/** The second person approves; a red-lane change (a new space, a deletion) repeats the resource's name. */
async function approve(approver: BrowserContext, change: Change, confirm: string): Promise<void> {
  if (change.status?.phase !== 'PendingApproval') return;
  const response = await approver.request.post(`/api/v1/projects/${PROJECT}/changes/${change.metadata.name}/approve`, {
    headers: await csrfHeaders(approver),
    data: { confirm },
  });
  expect(response.status(), `CC-34: the approver approves ${change.metadata.name}: ${await response.text()}`).toBe(202);
}

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: join(EVIDENCE, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

/** What the last failed read answered, for the message of a run that never saw its entities. */
let lastRefusal = '';

/** The scratch space's entities through its Endpoint, by the id suffix; `null` when the read failed. */
async function read(steward: BrowserContext): Promise<Map<string, Entity> | null> {
  const response = await steward.request.get(
    `/api/endpoint/${SLUG}/ngsi-ld/v1/entities?type=Vehicle,Alert&limit=100`,
    { headers: { accept: 'application/json' } },
  );
  if (response.status() !== 200) {
    lastRefusal = `${response.status()} ${(await response.text()).slice(0, 300)}`;
    return null;
  }
  const entities = (await response.json()) as Entity[];
  const bySuffix = new Map<string, Entity>();
  for (const suffix of [ROLLING, STEADY, ALERT]) {
    const entity = entities.find((e) => e.id.endsWith(suffix));
    if (entity) bySuffix.set(suffix, entity);
  }
  return bySuffix;
}

const at = (iso: string | undefined) => (iso ? Date.parse(iso) : NaN);

/** A fresh session of the person PORTAL_<who>_USER names, for the cleanup after the tests' own closed. */
async function session(browser: Browser, baseURL: string | undefined, who: string): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
  const page = await context.newPage();
  await page.goto('/');
  await signIn(page, requireEnv(`PORTAL${who}_USER`), requireEnv(`PORTAL${who}_PASSWORD`), baseURL);
  return context;
}

/** PF-57: the manifest's own check, green, records the verdict its proposal needs; then the proposal. */
async function propose(steward: BrowserContext, plural: string, doc: Record<string, unknown>): Promise<Change> {
  const name = (doc.metadata as { name: string }).name;
  const path = `/api/v1/projects/${PROJECT}/${plural}`;
  const check = await steward.request.post(`${path}?dryRun=All`, { headers: await csrfHeaders(steward), data: doc });
  const checked = await check.text();
  expect(check.status(), `${plural}/${name} is checked: ${checked}`).toBe(200);
  expect((JSON.parse(checked) as { verdict?: { ok?: boolean } }).verdict?.ok, `${plural}/${name} passes its check: ${checked}`).toBe(true);
  const proposal = await steward.request.post(path, { headers: await csrfHeaders(steward), data: doc });
  const proposed = await proposal.text();
  expect(proposal.status(), `${plural}/${name} is proposed: ${proposed}`).toBe(202);
  return JSON.parse(proposed) as Change;
}

test.describe('Journey 45: a pipeline lifetime ends entities and attributes on time (T-4843)', () => {
  test.describe.configure({ mode: 'serial', timeout: 2_400_000 });

  let proposed = false;
  let applied = false;
  /** Changes of this run still waiting for the approver: rejected in the cleanup, never left open. */
  const pending = new Set<string>();

  test.afterAll(async ({ browser, baseURL }) => {
    // Consumers before what they consume, whatever passed above; the space takes its entities.
    if (!proposed) return;
    const stewardContext = await session(browser, baseURL, '');
    const approverContext = await session(browser, baseURL, '_APPROVER');
    for (const id of pending) {
      await approverContext.request.post(`/api/v1/projects/${PROJECT}/changes/${id}/reject`, {
        headers: await csrfHeaders(approverContext),
        data: { reason: 'Journey 45 ended before this change was approved; its scratch resources are removed.' },
      }).catch(() => undefined);
    }
    for (const { plural, doc } of [...MANIFESTS].reverse()) {
      const name = (doc.metadata as { name: string }).name;
      const response = await stewardContext.request.delete(
        `/api/v1/projects/${PROJECT}/${plural}/${name}?confirm=${name}`,
        { headers: await csrfHeaders(stewardContext) },
      ).catch(() => undefined);
      if (response?.status() !== 202) continue;
      const change = (await response.json().catch(() => undefined)) as Change | undefined;
      if (change) await approve(approverContext, change, name).catch(() => undefined);
      await expect(async () => {
        const gone = await stewardContext!.request.get(`/api/v1/projects/${PROJECT}/${plural}/${name}`);
        expect(gone.status(), `${plural}/${name} is removed`).toBe(404);
      }).toPass({ timeout: 600_000, intervals: [10_000] }).catch(() => undefined);
    }
    await stewardContext.close();
    await approverContext.close();
  });

  test('the steward proposes the scratch space and its lifetime pipeline, the approver approves', async ({ steward, approver }) => {
    for (const { plural, doc } of MANIFESTS) {
      const name = (doc.metadata as { name: string }).name;
      const change = await propose(steward.context, plural, doc);
      proposed = true;
      pending.add(change.metadata.name);
      await approve(approver.context, change, name);
      pending.delete(change.metadata.name);
      // The next manifest names this one, so it waits until the change is merged and the
      // resource is there; a kind that reports a phase (a space, an Endpoint, a pipeline) is live.
      await expect(async () => {
        const answer = await steward.context.request.get(`/api/v1/projects/${PROJECT}/changes/${change.metadata.name}`);
        const phase = ((await answer.json()) as Change).status?.phase;
        expect(phase, `${plural}/${name}: change ${change.metadata.name} was rejected`).not.toBe('Rejected');
        expect(['Merged', 'Applied'], `${plural}/${name}: change ${change.metadata.name} is merged`).toContain(phase);
        const resource = await steward.context.request.get(`/api/v1/projects/${PROJECT}/${plural}/${name}`);
        expect(resource.status(), `${plural}/${name} exists`).toBe(200);
        const live = ((await resource.json()) as Change).status?.phase;
        if (live !== undefined) expect(live, `${plural}/${name} is live`).toMatch(/live|ready|applied|running/i);
      }).toPass({ timeout: Math.max(CUTOFF - Date.now(), 60_000), intervals: [5_000] });
    }
    applied = true;
  });

  test('each lifetime ends its target within one sweep, and nothing else ends', async ({ steward }) => {
    test.skip(!applied, 'the scratch space was not applied');
    // The three are written, each with the end its lifetime gives, before the cutoff.
    let first: Map<string, Entity> | null = null;
    await expect(async () => {
      first = await read(steward.context);
      const pipeline = await steward.context.request.get(`/api/v1/projects/${PROJECT}/pipelines/${NAME}`);
      const state = JSON.stringify(((await pipeline.json()) as { status?: unknown }).status ?? null).slice(0, 400);
      expect(first && [ROLLING, STEADY, ALERT].every((id) => first!.has(id)),
        `all three entities are written; read: ${first ? [...first.keys()].join(' ') || 'none' : lastRefusal}; pipeline: ${state}`).toBe(true);
    }, 'PL-82: the pipeline writes before the cutoff; a later first write means dev took longer than JC_LIFETIME_DEPLOY_MIN').toPass({
      timeout: Math.max(CUTOFF - Date.now() - MINUTE, 1), intervals: [POLL_MS],
    });
    const written = first as unknown as Map<string, Entity>;
    const rolling = written.get(ROLLING)!;
    expect(at(rolling.expiresAt) - Date.now(), 'the rolling end is the write time plus two minutes').toBeLessThanOrEqual(2 * MINUTE + 5_000);
    expect(at(rolling.expiresAt), 'the rolling end lies ahead').toBeGreaterThan(Date.now());
    expect(written.get(ALERT)!.expiresAt, 'the Alert ends at the fixed date').toBe(FIXED);
    const steadyLocation = written.get(STEADY)!.location as Attribute;
    expect(at(steadyLocation.expiresAt), 'the location carries its own end').toBeGreaterThan(Date.now());
    expect(written.get(STEADY)!.expiresAt, 'the steady vehicle renews its rolling end').toBeTruthy();
    await steward.page.goto(`/projects/${PROJECT}/spaces/${NAME}?lang=en`);
    await shot(steward.page, '45-01-space-written');
    await steward.page.goto(`/projects/${PROJECT}/pipelines/${NAME}?lang=en`);
    await shot(steward.page, '45-02-pipeline');

    // Each end, and the moment a read first misses what it ends.
    const due = { rolling: CUTOFF + 2 * MINUTE, location: CUTOFF + 2 * MINUTE, alert: at(FIXED) };
    const gone: Partial<Record<keyof typeof due, number>> = {};
    const until = due.alert + SWEEP_MS + POLL_MS;
    while (Date.now() < until) {
      const now = Date.now();
      const entities = await read(steward.context);
      if (entities) {
        const steady = entities.get(STEADY);
        expect(steady, 'nothing else ends: the steady vehicle stays').toBeTruthy();
        expect((steady!.speed as Attribute | undefined)?.value, 'nothing else ends: its speed stays').toBe(1);
        if (!entities.has(ROLLING)) gone.rolling ??= now;
        if (!steady!.location) gone.location ??= now;
        if (!entities.has(ALERT)) gone.alert ??= now;
        // Before its end minus a write period, a target is still there.
        if (now < CUTOFF - MINUTE) expect(gone.rolling ?? gone.location, 'nothing ends while it is written').toBeUndefined();
        if (now < due.alert - POLL_MS) expect(gone.alert, 'the Alert lives until its fixed date').toBeUndefined();
      }
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
    for (const key of Object.keys(due) as Array<keyof typeof due>) {
      expect(gone[key], `${key} ends`).toBeDefined();
      expect(gone[key]! - due[key], `${key} ends within one sweep after its end`).toBeLessThanOrEqual(SWEEP_MS);
    }
    await steward.page.goto(`/projects/${PROJECT}/spaces/${NAME}?lang=en`);
    await shot(steward.page, '45-03-space-after');
  });
});

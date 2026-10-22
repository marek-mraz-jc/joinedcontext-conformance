import { randomBytes } from 'node:crypto';
import { request as httpsRequest } from 'node:https';
import type { Browser, BrowserContext } from '@playwright/test';
import { test, expect, requireEnv, signIn } from '../fixtures/portal.js';
import { Recorder, type Side } from '../ws-isolation/verdict.js';

/**
 * Journey 48 (T-4909, T-4872 step 3, T-4874, docs API/07, EP-61, EP-106): WebSocket isolation
 * through the real edge. The steward proposes a sandbox space with two Endpoints over the same
 * type with different projections (one anonymous and public, one for the conformance service
 * account), a second sandbox space with the same entity ids, and the Policies; the approver
 * approves each. Then sockets stay open on both Endpoints while the service account writes for
 * JC_WSISO_MINUTES (default 30): Alert creates, shown updates, hidden-only updates, Alerts whose
 * Relationship names a GtfsStop, GtfsStop writes, and the same ids in the second space. A REST read
 * with join=inline runs beside each round.
 *
 * Canaries only: the attribute every Endpoint hides (`cause`), the type no Endpoint shows
 * (`GtfsStop`) and every value of the second space carry a marker of this run, and `alertSource`
 * carries the authenticated-only marker. The run fails on any marker byte or canary entity on any
 * socket, any notification for a hidden-only update, any authenticated-only attribute on the
 * anonymous socket (ws-isolation/verdict.ts), a token socket that outlives its token or 300 s, and
 * an anonymous socket that closes on its own. At the end the public Endpoint is narrowed to the
 * organization through a Change: its open socket gets `x-closing` `endpoint-changed`, closes 1000,
 * nothing follows, and a new anonymous upgrade is refused 401. Afterwards the journey deletes what
 * it proposed; both spaces are sandboxes with a one-day TTL.
 *
 * Accounts: PORTAL_USER/PASSWORD (a steward of JC_WSISO_PROJECT, default helsinki),
 * PORTAL_APPROVER_USER/PASSWORD, and the conformance service account's client secret
 * NGSILD_CLIENT_SECRET (client banskabystrica-conformance, whose token names the two fixed slugs
 * below as audiences: deployment components/context-gateway/keycloak-clients.yaml). One run at a
 * time: the slugs are fixed.
 */
const PROJECT = process.env.JC_WSISO_PROJECT ?? 'helsinki';
const MODEL = process.env.JC_WSISO_MODEL ?? 'helsinki';
const ORG = process.env.JC_WSISO_ORG ?? 'hel.fi';
const MINUTES = Number(process.env.JC_WSISO_MINUTES ?? '30');
const API = (process.env.JC_WSISO_API_URL ?? 'https://api.dev.joinedcontext.com').replace(/\/$/, '');
const TOKEN_URL = process.env.JC_WSISO_TOKEN_URL ?? 'https://idm.dev.joinedcontext.com/realms/dev/protocol/openid-connect/token';
const CLIENT_ID = process.env.JC_WSISO_CLIENT_ID ?? 'banskabystrica-conformance';
/** The conformance account the writes and the authenticated socket run as, `{project}/{name}`. */
const WRITER = process.env.JC_WSISO_WRITER ?? 'banskabystrica/conformance';
const API_VERSION = 'joinedcontext.com/v1alpha1';
const RUN = randomBytes(4).toString('hex');
const NAME = `t4909-${RUN}`;
const OTHER = `${NAME}-b`;
const CANARY = `canary-${RUN}`;
const OTHER_MARK = `otherspace-${RUN}`;
const AUTH_ONLY = `authonly-${RUN}`;
const SHOWN = 'Alert';
const CANARY_TYPE = 'GtfsStop';
const HIDDEN = 'cause';
const AUTH_ONLY_ATTR = 'alertSource';
const PROTOCOL = 'ngsi-ld-ws';
const WAIT_MS = 20_000;
const ROUND_MS = 20_000;
/** API/07 §1: a token socket ends at its token's exp, and after 300 s at the latest. */
const TOKEN_SOCKET_MS = 300_000;
const GRACE_MS = 10_000;

/** 32 characters of lowercase base32 (160 bits), the Endpoint slug EP-02 asks for. */
function slug(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567';
  return [...randomBytes(32)].map((byte) => alphabet[byte % 32]).join('');
}

const PUBLIC_SLUG = slug();
// Fixed: the conformance client's audience mappers name them (T-4909); nothing else holds them.
const MEMBER_SLUG = 'hcwfp3wxr45cjxjwqsfcyxgv4klxb2ob';
const OTHER_SLUG = 'mieqdukdwfv24izi5qxkia3e3qolhbvp';

const READ_OPS = ['retrieveOps', 'createSubscription', 'retrieveSubscription', 'updateSubscription', 'deleteSubscription'];
const WRITE_OPS = ['createEntity', 'appendAttrs', 'updateAttrs', 'deleteEntity'];
const BOTH_TYPES = [{ entities: [{ type: SHOWN }, { type: CANARY_TYPE }] }];

function space(name: string): Record<string, unknown> {
  return {
    apiVersion: API_VERSION, kind: 'ContextSpace',
    metadata: { name, namespace: PROJECT, title: { en: `Journey 48 WebSocket isolation ${name}` } },
    spec: {
      isSandbox: true, ttlDays: 1, defaultLocale: 'en',
      dataModelRef: { kind: 'DataModel', name: MODEL },
      retention: [{ entityType: SHOWN, history: 'off' }, { entityType: CANARY_TYPE, history: 'off' }],
    },
  };
}

function endpoint(name: string, spaceName: string, endpointSlug: string, audience: string, hidden: string[]): Record<string, unknown> {
  return {
    apiVersion: API_VERSION, kind: 'Endpoint',
    metadata: { name, namespace: PROJECT, title: { en: `Journey 48 ${name}` } },
    spec: {
      contextSpaceRef: spaceName, slug: endpointSlug, audience,
      enabledRepresentations: ['ngsi-ld', 'websocket'],
      projection: { hiddenAttributes: hidden },
    },
  };
}

function policy(name: string, spaceName: string, assignee: Record<string, string>, operations: string[], information: unknown[]): Record<string, unknown> {
  return {
    apiVersion: API_VERSION, kind: 'Policy',
    metadata: { name, namespace: PROJECT, title: { en: `Journey 48 ${name}` } },
    spec: {
      contextSpaceRef: { kind: 'ContextSpace', name: spaceName }, assigner: `did:web:${ORG}`,
      assignee, operations, information,
    },
  };
}

const PUBLIC_ENDPOINT = endpoint(`${NAME}-public`, NAME, PUBLIC_SLUG, 'public', [HIDDEN, AUTH_ONLY_ATTR]);
const MANIFESTS: Array<{ plural: string; doc: Record<string, unknown> }> = [
  { plural: 'spaces', doc: space(NAME) },
  { plural: 'spaces', doc: space(OTHER) },
  { plural: 'endpoints', doc: PUBLIC_ENDPOINT },
  { plural: 'endpoints', doc: endpoint(`${NAME}-auth`, NAME, MEMBER_SLUG, 'organization', [HIDDEN]) },
  { plural: 'endpoints', doc: endpoint(OTHER, OTHER, OTHER_SLUG, 'organization', []) },
  // Reads name only the shown type, so GtfsStop is the type no Endpoint shows.
  { plural: 'policies', doc: policy(`${NAME}-public-read`, NAME, { kind: 'role', id: 'public' }, READ_OPS, [{ entities: [{ type: SHOWN }] }]) },
  { plural: 'policies', doc: policy(`${NAME}-read`, NAME, { kind: 'serviceAccount', id: WRITER }, READ_OPS, [{ entities: [{ type: SHOWN }] }]) },
  { plural: 'policies', doc: policy(`${NAME}-write`, NAME, { kind: 'serviceAccount', id: WRITER }, WRITE_OPS, BOTH_TYPES) },
  { plural: 'policies', doc: policy(`${OTHER}-write`, OTHER, { kind: 'serviceAccount', id: WRITER }, WRITE_OPS, BOTH_TYPES) },
];

type Change = { metadata: { name: string }; status?: { phase?: string } };

async function csrfHeaders(context: BrowserContext): Promise<Record<string, string>> {
  const csrf = (await context.cookies()).find((cookie) => cookie.name === '__Host-jc_csrf');
  expect(csrf, 'the session carries a CSRF cookie').toBeTruthy();
  return { 'x-csrf-token': csrf?.value ?? '', 'content-type': 'application/json' };
}

async function approve(approver: BrowserContext, change: Change, confirm: string): Promise<void> {
  if (change.status?.phase !== 'PendingApproval') return;
  const response = await approver.request.post(`/api/v1/projects/${PROJECT}/changes/${change.metadata.name}/approve`, {
    headers: await csrfHeaders(approver),
    data: { confirm },
  });
  expect(response.status(), `CC-34: the approver approves ${change.metadata.name}: ${await response.text()}`).toBe(202);
}

/** PF-57: the manifest's own check, green, then the proposal (POST creates, PUT changes). */
async function propose(steward: BrowserContext, plural: string, doc: Record<string, unknown>, method: 'post' | 'put' = 'post'): Promise<Change> {
  const name = (doc.metadata as { name: string }).name;
  const path = `/api/v1/projects/${PROJECT}/${plural}${method === 'put' ? `/${name}` : ''}`;
  const check = await steward.request[method](`${path}?dryRun=All`, { headers: await csrfHeaders(steward), data: doc });
  const checked = await check.text();
  expect(check.status(), `${plural}/${name} is checked: ${checked}`).toBe(200);
  expect((JSON.parse(checked) as { verdict?: { ok?: boolean } }).verdict?.ok, `${plural}/${name} passes its check: ${checked}`).toBe(true);
  const proposal = await steward.request[method](path, { headers: await csrfHeaders(steward), data: doc });
  const proposed = await proposal.text();
  expect(proposal.status(), `${plural}/${name} is proposed: ${proposed}`).toBe(202);
  return JSON.parse(proposed) as Change;
}

/** Until the change is merged and the resource reports itself live. */
async function applied(steward: BrowserContext, plural: string, name: string, change: Change, timeout: number): Promise<void> {
  await expect(async () => {
    const answer = await steward.request.get(`/api/v1/projects/${PROJECT}/changes/${change.metadata.name}`);
    const phase = ((await answer.json()) as Change).status?.phase;
    expect(phase, `${plural}/${name}: change ${change.metadata.name} was rejected`).not.toBe('Rejected');
    expect(['Merged', 'Applied'], `${plural}/${name}: change ${change.metadata.name} is merged`).toContain(phase);
    const resource = await steward.request.get(`/api/v1/projects/${PROJECT}/${plural}/${name}`);
    expect(resource.status(), `${plural}/${name} exists`).toBe(200);
    const live = ((await resource.json()) as Change).status?.phase;
    if (live !== undefined) expect(live, `${plural}/${name} is live`).toMatch(/live|ready|applied|running/i);
  }).toPass({ timeout, intervals: [5_000] });
}

async function session(browser: Browser, baseURL: string | undefined, who: string): Promise<BrowserContext> {
  const context = await browser.newContext({ baseURL, locale: 'en-GB' });
  const page = await context.newPage();
  await page.goto('/');
  await signIn(page, requireEnv(`PORTAL${who}_USER`), requireEnv(`PORTAL${who}_PASSWORD`), baseURL);
  return context;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The conformance account's token, minted again a minute before it expires; never logged. */
const tokens = { value: '', exp: 0 };
async function token(): Promise<{ value: string; exp: number }> {
  if (tokens.exp - Date.now() > 60_000) return tokens;
  const answer = await fetch(TOKEN_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'client_credentials', client_id: CLIENT_ID, client_secret: requireEnv('NGSILD_CLIENT_SECRET') }),
    signal: AbortSignal.timeout(WAIT_MS),
  });
  expect(answer.status, 'the conformance account gets a token (its client secret is NGSILD_CLIENT_SECRET)').toBe(200);
  const body = (await answer.json()) as { access_token: string; expires_in: number };
  tokens.value = body.access_token;
  tokens.exp = Date.now() + body.expires_in * 1000;
  return tokens;
}

const ngsild = (endpointSlug: string) => `${API}/api/endpoint/${endpointSlug}/ngsi-ld/v1`;

/** One NGSI-LD call as the conformance account; a refusal fails the run with the answer. */
async function call(method: string, url: string, body?: unknown): Promise<string> {
  const answer = await fetch(url, {
    method,
    headers: { authorization: `Bearer ${(await token()).value}`, 'content-type': 'application/json', accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(WAIT_MS),
  });
  const text = await answer.text();
  expect(answer.ok, `${method} ${url.replace(API, '')} answered ${answer.status}: ${text.slice(0, 300)}`).toBe(true);
  return text;
}

const property = (value: unknown) => ({ type: 'Property', value });
const id = (type: string, local: string) => `urn:ngsi-ld:${type}:${NAME}-${local}`;

/** An Alert with its shown, authenticated-only and hidden attributes and a Relationship to a canary stop. */
function alert(local: string, severity: string, stop: string, mark?: string): Record<string, unknown> {
  return {
    id: id(SHOWN, local), type: SHOWN,
    category: property(mark ?? 'traffic'),
    severity: property(mark ?? severity),
    [AUTH_ONLY_ATTR]: property(mark ?? AUTH_ONLY),
    [HIDDEN]: property(mark ?? CANARY),
    refStop: { type: 'Relationship', object: [stop] },
  };
}

function stop(local: string, mark: string): Record<string, unknown> {
  return { id: id(CANARY_TYPE, local), type: CANARY_TYPE, stopCode: property(mark), name: { type: 'LanguageProperty', languageMap: { en: mark } } };
}

/** One socket on one Endpoint, every frame fed to the recorder. */
class Conn {
  static next = 0;
  readonly channel = `ws:${++Conn.next}`;
  readonly opened = Date.now();
  ws!: WebSocket;
  closed?: { at: number; code: number; reason: string };
  reasons: string[] = [];
  private readonly pending = new Map<string, (status: number) => void>();

  constructor(readonly side: Side, readonly recorder: Recorder, readonly deadline: number) {}

  async open(url: string, bearer?: string): Promise<this> {
    this.ws = new WebSocket(url, bearer ? [PROTOCOL, `bearer.${bearer}`] : [PROTOCOL]);
    this.ws.onmessage = (event) => {
      const text = typeof event.data === 'string' ? event.data : String(event.data);
      this.recorder.frame(this.side, this.channel, text);
      try {
        const metadata = (JSON.parse(text) as { metadata?: Record<string, unknown> }).metadata ?? {};
        if (metadata['x-op'] === 'x-closing') this.reasons.push(String(metadata['x-reason']));
        if (metadata['x-op'] === 'x-result') this.pending.get(String(metadata['x-id']))?.(Number(metadata['x-status']));
      } catch {
        // judged by the recorder already
      }
    };
    this.ws.onclose = (event) => {
      this.closed = { at: Date.now(), code: event.code, reason: event.reason };
    };
    await new Promise<void>((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = () => reject(new Error(`${this.side} socket ${url.replace(/bearer\.[^,]+/, '')} did not open`));
    });
    return this;
  }

  /** x-subscribe, answered with its x-status. */
  subscribe(xid: string, subscription: Record<string, unknown>): Promise<number> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${this.side} ${this.channel}: no x-result for ${xid}`)), WAIT_MS);
      this.pending.set(xid, (status) => {
        clearTimeout(timer);
        resolve(status);
      });
      this.ws.send(JSON.stringify({ metadata: { 'x-op': 'x-subscribe', 'x-id': xid }, body: { type: 'Subscription', ...subscription } }));
    });
  }
}

test.describe('Journey 48: WebSocket isolation through the edge (T-4909)', () => {
  test.describe.configure({ mode: 'serial', timeout: (MINUTES + 30) * 60_000 });

  let proposed = false;
  let live = false;
  const pending = new Set<string>();

  test.afterAll(async ({ browser, baseURL }) => {
    if (!proposed) return;
    const steward = await session(browser, baseURL, '');
    const approver = await session(browser, baseURL, '_APPROVER');
    const failures: string[] = [];
    const reason = (error: unknown) => (error instanceof Error ? error.message.split('\n')[0] : String(error));
    for (const change of pending) {
      try {
        const rejected = await approver.request.post(`/api/v1/projects/${PROJECT}/changes/${change}/reject`, {
          headers: await csrfHeaders(approver),
          data: { reason: 'Journey 48 ended before this change was approved; its sandbox resources are removed.' },
        });
        if (!rejected.ok()) failures.push(`reject ${change}: ${rejected.status()} ${await rejected.text()}`);
      } catch (error) {
        failures.push(`reject ${change}: ${reason(error)}`);
      }
    }
    // Consumers before what they consume; a space's deletion takes its entities.
    for (const { plural, doc } of [...MANIFESTS].reverse()) {
      const name = (doc.metadata as { name: string }).name;
      try {
        const response = await steward.request.delete(`/api/v1/projects/${PROJECT}/${plural}/${name}?confirm=${name}`, {
          headers: await csrfHeaders(steward),
        });
        if (response.status() === 404) continue;
        if (response.status() !== 202) {
          failures.push(`delete ${plural}/${name}: ${response.status()} ${await response.text()}`);
          continue;
        }
        await approve(approver, (await response.json()) as Change, name);
        await expect(async () => {
          const gone = await steward.request.get(`/api/v1/projects/${PROJECT}/${plural}/${name}`);
          expect(gone.status(), `${plural}/${name} is removed`).toBe(404);
        }).toPass({ timeout: 600_000, intervals: [10_000] });
      } catch (error) {
        failures.push(`delete ${plural}/${name}: ${reason(error)}`);
      }
    }
    await steward.close();
    await approver.close();
    expect(failures, 'the sandbox resources are all removed').toEqual([]);
  });

  test('the steward proposes two sandbox spaces, three Endpoints and four Policies, the approver approves', async ({ steward, approver }) => {
    requireEnv('NGSILD_CLIENT_SECRET');
    for (const { plural, doc } of MANIFESTS) {
      const name = (doc.metadata as { name: string }).name;
      const change = await propose(steward.context, plural, doc);
      proposed = true;
      pending.add(change.metadata.name);
      await approve(approver.context, change, name);
      pending.delete(change.metadata.name);
      await applied(steward.context, plural, name, change, 900_000);
    }
    live = true;
  });

  test('no canary reaches either socket in 30 minutes of mixed writes, and a narrowed Endpoint closes its socket', async ({ steward, approver }) => {
    test.skip(!live, 'the sandbox was not applied');
    const recorder = new Recorder({
      canaryMarkers: [CANARY, OTHER_MARK],
      authOnlyMarker: AUTH_ONLY,
      canaryType: CANARY_TYPE,
      hiddenAttribute: HIDDEN,
      authOnlyAttribute: AUTH_ONLY_ATTR,
    });
    const problems: string[] = [];
    const wsUrl = (endpointSlug: string) => `${API.replace(/^http/, 'ws')}/api/endpoint/${endpointSlug}/ws`;

    /** A socket with its subscriptions: the shown type, the shown type joined inline, the canary type. */
    async function connect(side: Side): Promise<Conn> {
      const bearer = side === 'authenticated' ? await token() : undefined;
      const deadline = bearer ? Math.min(bearer.exp, Date.now() + TOKEN_SOCKET_MS) : Number.POSITIVE_INFINITY;
      const conn = await new Conn(side, recorder, deadline).open(wsUrl(side === 'anonymous' ? PUBLIC_SLUG : MEMBER_SLUG), bearer?.value);
      expect(await conn.subscribe('type', { entities: [{ type: SHOWN }] }), `${side}: the shown type is subscribed`).toBe(201);
      const joined = await conn.subscribe('join', { entities: [{ type: SHOWN }], notification: { join: 'inline', joinLevel: 1 } });
      if (joined !== 201) test.info().annotations.push({ type: 'join', description: `${side}: a subscription with join=inline answered ${joined}` });
      // Allowed or refused; whatever it notifies is judged as a canary-type leak.
      await conn.subscribe('canary', { entities: [{ type: CANARY_TYPE }] });
      return conn;
    }

    const conns: Record<Side, Conn> = { anonymous: await connect('anonymous'), authenticated: await connect('authenticated') };
    let narrowing = false;

    /** A closed socket is judged and replaced; a token socket past its deadline is a finding. */
    async function keep(): Promise<void> {
      for (const side of ['anonymous', 'authenticated'] as Side[]) {
        const conn = conns[side];
        if (!conn.closed) {
          if (Date.now() > conn.deadline + GRACE_MS) {
            problems.push(`${side} ${conn.channel} outlived its token (API/07 §1)`);
            conn.ws.close();
          }
          continue;
        }
        if (side === 'anonymous' && !narrowing) {
          problems.push(`anonymous ${conn.channel} closed on its own: ${conn.closed.code} ${conn.reasons.join(',')}`);
        }
        if (side === 'anonymous' && narrowing) continue;
        conns[side] = await connect(side);
      }
    }

    /** Waits until each side's current socket saw `entityId`, then seals it there. */
    async function sealWhenSeen(entityId: string): Promise<void> {
      const until = Date.now() + WAIT_MS;
      while (Date.now() < until && !(['anonymous', 'authenticated'] as Side[]).every((side) => recorder.hasSeen(conns[side].channel, entityId))) {
        await sleep(250);
      }
      const channels = [conns.anonymous.channel, conns.authenticated.channel];
      const sealed = recorder.seal(entityId, channels);
      for (const side of ['anonymous', 'authenticated'] as Side[]) {
        if (!sealed.includes(conns[side].channel) && !conns[side].closed) {
          problems.push(`${side} ${conns[side].channel} was not notified of the shown create ${entityId} within ${WAIT_MS} ms`);
        }
      }
    }

    const end = Date.now() + MINUTES * 60_000;
    let round = 0;
    while (Date.now() < end) {
      const started = Date.now();
      await keep();
      const canaryStop = id(CANARY_TYPE, `c${round % 3}`);
      // The canary type: created in the first rounds, then updated.
      if (round < 3) await call('POST', `${ngsild(MEMBER_SLUG)}/entities`, stop(`c${round}`, CANARY));
      else await call('PATCH', `${ngsild(MEMBER_SLUG)}/entities/${encodeURIComponent(canaryStop)}/attrs`, { stopCode: property(CANARY) });
      // A shown create whose Relationship names the canary stop, and a shown update of the last one.
      await call('POST', `${ngsild(MEMBER_SLUG)}/entities`, alert(`a${round}`, 'informational', canaryStop));
      if (round > 0) {
        await call('PATCH', `${ngsild(MEMBER_SLUG)}/entities/${encodeURIComponent(id(SHOWN, `a${round - 1}`))}/attrs`, { severity: property('medium') });
      }
      // A hidden-only target: created, seen, sealed, then changed only in the hidden attribute.
      const hiddenOnly = id(SHOWN, `h${round}`);
      await call('POST', `${ngsild(MEMBER_SLUG)}/entities`, alert(`h${round}`, 'informational', canaryStop));
      await sealWhenSeen(hiddenOnly);
      for (const n of [1, 2]) {
        await call('PATCH', `${ngsild(MEMBER_SLUG)}/entities/${encodeURIComponent(hiddenOnly)}/attrs`, { [HIDDEN]: property(`${CANARY}-${n}`) });
      }
      // The same ids in the second space, every value its marker.
      await call('POST', `${ngsild(OTHER_SLUG)}/entities`, alert(`a${round}`, '', canaryStop, OTHER_MARK));
      await call('POST', `${ngsild(OTHER_SLUG)}/entities`, alert(`h${round}`, '', canaryStop, OTHER_MARK));
      if (round < 3) await call('POST', `${ngsild(OTHER_SLUG)}/entities`, stop(`c${round}`, OTHER_MARK));
      // The REST reads beside the sockets, joined inline.
      const query = `entities?type=${SHOWN}&join=inline&joinLevel=1&limit=100`;
      const anonymous = await fetch(`${ngsild(PUBLIC_SLUG)}/${query}`, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(WAIT_MS) });
      recorder.frame('anonymous', 'rest', await anonymous.text());
      recorder.frame('authenticated', 'rest', await call('GET', `${ngsild(MEMBER_SLUG)}/${query}`));
      // Old ones go, unsealed first: a deletion is a change a socket may be told of.
      if (round >= 4) {
        for (const local of [`a${round - 4}`, `h${round - 4}`]) {
          recorder.seal(id(SHOWN, local), []);
          await call('DELETE', `${ngsild(MEMBER_SLUG)}/entities/${encodeURIComponent(id(SHOWN, local))}`);
        }
      }
      round += 1;
      await sleep(Math.max(ROUND_MS - (Date.now() - started), 0));
    }
    await keep();
    expect(recorder.report(), 'no canary, hidden attribute or authenticated-only attribute reaches a socket').toBe('');
    expect(problems, 'every socket stayed within its lifetime and saw every shown create').toEqual([]);
    expect(recorder.notified.anonymous, 'the anonymous socket was notified').toBeGreaterThan(round);
    expect(recorder.notified.authenticated, 'the authenticated socket was notified').toBeGreaterThan(round);

    // T-4874: narrowing the public Endpoint closes its open socket, and nothing follows the close.
    narrowing = true;
    const watched = conns.anonymous;
    const spec = { ...(PUBLIC_ENDPOINT.spec as Record<string, unknown>), audience: 'organization' };
    const narrowed = { ...PUBLIC_ENDPOINT, spec };
    const name = (PUBLIC_ENDPOINT.metadata as { name: string }).name;
    const change = await propose(steward.context, 'endpoints', narrowed, 'put');
    pending.add(change.metadata.name);
    await approve(approver.context, change, name);
    pending.delete(change.metadata.name);
    const closeBy = Date.now() + 900_000;
    let n = 0;
    while (!watched.closed && Date.now() < closeBy) {
      await call('PATCH', `${ngsild(MEMBER_SLUG)}/entities/${encodeURIComponent(id(SHOWN, `a${round - 1}`))}/attrs`, { severity: property(`high-${n++}`) });
      await sleep(5_000);
    }
    expect(watched.closed, 'the anonymous socket closes once the narrowing is loaded').toBeTruthy();
    expect(watched.reasons, 'x-closing names endpoint-changed (API/07 §1)').toEqual(['endpoint-changed']);
    expect(watched.closed?.code, 'the close is a normal one').toBe(1000);
    expect(await upgradeStatus(wsUrl(PUBLIC_SLUG)), 'a new anonymous upgrade is refused once the Endpoint is not public').toBe(401);
    await keep();
    expect(recorder.report(), 'nothing leaked while the Endpoint was narrowed').toBe('');
    for (const conn of Object.values(conns)) conn.ws.close();
  });
});

/** The HTTP status a WebSocket upgrade answers, read from the response the WebSocket API hides. */
function upgradeStatus(url: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const req = httpsRequest(url.replace(/^wss/, 'https'), {
      headers: {
        Connection: 'Upgrade', Upgrade: 'websocket', 'Sec-WebSocket-Version': '13',
        'Sec-WebSocket-Key': randomBytes(16).toString('base64'), 'Sec-WebSocket-Protocol': PROTOCOL,
      },
      timeout: WAIT_MS,
    });
    req.on('upgrade', (_response, socket) => {
      socket.destroy();
      resolve(101);
    });
    req.on('response', (response) => {
      response.resume();
      resolve(response.statusCode ?? 0);
    });
    req.on('timeout', () => req.destroy(new Error(`${url}: no answer to the upgrade`)));
    req.on('error', reject);
    req.end();
  });
}

import { test, expect, openView, openProject } from '../fixtures/portal.js';
import type { APIRequestContext, BrowserContext, Page } from '@playwright/test';
import { execFile } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * Journey 34 (T-4047, T-3838): a camera publishes over MQTT and its frames become Device and
 * DeviceMeasurement entities behind an Endpoint (MF-54, PL-76, PF-47, ADR-N-048).
 *
 * The steward instantiates "Camera over MQTT" from the gallery, the approver approves (CC-34),
 * the broker goes Live, `jc-camera-sim publish` sends frames with a key whose private half stays
 * in this run's temp dir, and the Endpoint's count equals what the simulator says it published.
 * Then three refusals: a key no ServiceAccount declares, a ServiceAccount the broker does not
 * name, and a write with the camera's token. Everything is removed through the Portal afterwards.
 *
 * Data sources: N is the simulator's "published N of M" line; the entity count is the Endpoint's
 * `NGSILD-Results-Count` header; the edges are the `data-edge` rows of the brokers page.
 *
 * The simulator runs from `JC_CAMERA_SIM` (a binary path) or, without it, from the platform image
 * `JC_PLATFORM_IMAGE` through docker. Names end in four digits so the smoke janitor finds residue.
 */
const PROJECT = process.env.PORTAL_PROJECT || 'helsinki';
const SPACE = process.env.CAMERA_SPACE || 'helsinki';
const ORG = process.env.JC_ORG_DOMAIN || 'hel.fi';
const IDM = process.env.IDM_URL || 'https://idm.dev.joinedcontext.com';
const REALM = process.env.KEYCLOAK_REALM || 'dev';
const OWNER = process.env.CAMERA_OWNER || process.env.PORTAL_USER || '';
const FRAMES = process.env.CAMERA_FRAMES || '5';
const TOKEN_URL = `${IDM}/realms/${REALM}/protocol/openid-connect/token`;
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';

const suffix = String(1000 + crypto.randomInt(9000));
const NAME = `cam-${suffix}`;
const CAMERA = `camera-${suffix}`;
const STRANGER = `stranger-${suffix}`;
const DEVICE = `urn:ngsi-ld:Device:${ORG}:${SPACE}:${CAMERA}`;

const gallery = /flow gallery|blueprint gallery|galéria/i;
const approvals = /approvals|schválenia/i;
const brokers = /mqtt brokers|mqtt brokery/i;

interface CameraKey { dir: string; kid: string; x: string; y: string; pem: string }

/** A P-256 key pair in its own mode-700 dir; the private key never leaves it. */
function cameraKey(root: string, label: string): CameraKey {
  const dir = fs.mkdtempSync(path.join(root, `${label}-`));
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ec', { namedCurve: 'P-256' });
  const pem = path.join(dir, 'camera.pem');
  fs.writeFileSync(pem, privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600 });
  const jwk = publicKey.export({ format: 'jwk' });
  return { dir, kid: `camera-${crypto.randomBytes(4).toString('hex')}`, x: jwk.x as string, y: jwk.y as string, pem };
}

interface SimRun { code: number; stdout: string; stderr: string }

/** Runs `jc-camera-sim publish` for `client` with `key`; never prints the key or a token. */
function publish(key: CameraKey, client: string, broker: string): Promise<SimRun> {
  const env: Record<string, string> = {
    JC_CAMERA_TOKEN_URL: TOKEN_URL,
    JC_CAMERA_CLIENT_ID: `${PROJECT}-${client}`,
    JC_CAMERA_KID: key.kid,
    JC_CAMERA_BROKER: `tls://${broker}`,
    JC_CAMERA_COUNT: FRAMES,
    JC_CAMERA_INTERVAL_MS: '200',
  };
  const binary = process.env.JC_CAMERA_SIM;
  let file: string;
  let args: string[];
  let childEnv: NodeJS.ProcessEnv;
  if (binary) {
    file = binary;
    args = ['publish'];
    childEnv = { ...process.env, ...env, JC_CAMERA_KEY: key.pem };
  } else {
    const image = process.env.JC_PLATFORM_IMAGE;
    if (!image) {
      throw new Error('JC_CAMERA_SIM or JC_PLATFORM_IMAGE must name the simulator, see e2e/README.md');
    }
    file = 'docker';
    args = ['run', '--rm', '--user', `${process.getuid?.() ?? 0}`, '-v', `${key.dir}:/key:ro`, '-e', 'JC_CAMERA_KEY=/key/camera.pem'];
    for (const name of Object.keys(env)) args.push('-e', name);
    args.push(image, 'jc-camera-sim', 'publish');
    childEnv = { ...process.env, ...env };
  }
  return new Promise((resolve) => {
    execFile(file, args, { env: childEnv, timeout: 120_000 }, (error, stdout, stderr) => {
      const code = error ? (typeof error.code === 'number' ? error.code : 1) : 0;
      resolve({ code, stdout: String(stdout), stderr: String(stderr) });
    });
  });
}

/** The camera's own token: an RFC 7523 assertion signed ES256 with its key, as the simulator does. */
async function cameraToken(request: APIRequestContext, key: CameraKey, client: string): Promise<string> {
  const b64 = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const now = Math.floor(Date.now() / 1000);
  const clientId = `${PROJECT}-${client}`;
  const unsigned = `${b64({ alg: 'ES256', typ: 'JWT', kid: key.kid })}.${b64({
    iss: clientId, sub: clientId, aud: TOKEN_URL, jti: crypto.randomUUID(), iat: now, exp: now + 60,
  })}`;
  const signature = crypto.sign('sha256', Buffer.from(unsigned), {
    key: fs.readFileSync(key.pem), dsaEncoding: 'ieee-p1363',
  }).toString('base64url');
  const response = await request.post(TOKEN_URL, {
    form: {
      grant_type: 'client_credentials',
      client_id: clientId,
      client_assertion_type: 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
      client_assertion: `${unsigned}.${signature}`,
    },
  });
  expect(response.status(), 'PF-47: the camera signs in with its own key').toBe(200);
  return (await response.json()).access_token as string;
}

async function csrfHeaders(context: BrowserContext): Promise<Record<string, string>> {
  const csrf = (await context.cookies()).find((cookie) => cookie.name === '__Host-jc_csrf');
  expect(csrf, 'the session carries a CSRF cookie').toBeTruthy();
  return { 'x-csrf-token': csrf?.value ?? '', 'content-type': 'application/json' };
}

/** Approves change `id` as the second person; deletions are red and repeat the resource name. */
async function approveChange(approver: BrowserContext, id: string, confirm?: string): Promise<void> {
  const response = await approver.request.post(`/api/v1/projects/${PROJECT}/changes/${id}/approve`, {
    headers: await csrfHeaders(approver),
    data: confirm ? { confirm } : {},
  });
  expect(response.status(), `CC-34: the approver approves ${id}`).toBe(202);
}

async function screenshot(page: Page, name: string): Promise<void> {
  const dir = path.join(reportDir, 'journey-34');
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
}

/** `NGSILD-Results-Count` of the camera's measurements, read through the steward's session. */
async function measurements(steward: BrowserContext, slug: string): Promise<number> {
  const q = encodeURIComponent(`refDevice=="${DEVICE}"`);
  const response = await steward.request.get(
    `/api/endpoint/${slug}/ngsi-ld/v1/entities?type=DeviceMeasurement&q=${q}&count=true&limit=0`,
  );
  if (response.status() !== 200) return -1;
  return Number(response.headers()['ngsild-results-count'] ?? -1);
}

test.describe('Journey 34: camera over MQTT, simulator count equals the Endpoint (T-4047)', () => {
  test.describe.configure({ mode: 'serial', timeout: 900_000 });

  let keyRoot = '';
  let slug = '';
  let broker = '';
  let stewardContext: BrowserContext | undefined;
  let approverContext: BrowserContext | undefined;
  let camera: CameraKey | undefined;

  test.beforeAll(() => {
    keyRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'jc-camera-'));
    fs.chmodSync(keyRoot, 0o700);
  });

  test.afterAll(async () => {
    // Remove what the run created, consumers before what they consume, whatever passed above.
    const order: Array<[string, string]> = [
      ['pipelines', NAME], ['endpoints', NAME], ['datasources', NAME], ['mqttbrokers', NAME],
      ['serviceaccounts', CAMERA], ['serviceaccounts', STRANGER],
    ];
    if (stewardContext && approverContext) {
      for (const [plural, name] of order) {
        const response = await stewardContext.request.delete(
          `/api/v1/projects/${PROJECT}/${plural}/${name}?confirm=${name}`,
          { headers: await csrfHeaders(stewardContext) },
        ).catch(() => undefined);
        if (response?.status() !== 202) continue;
        const change = await response.json().catch(() => undefined);
        if (change?.status?.phase === 'PendingApproval') {
          await approveChange(approverContext, change.metadata.name, name).catch(() => undefined);
        }
      }
    }
    fs.rmSync(keyRoot, { recursive: true, force: true });
  });

  test('the steward proposes the camera, the approver approves, the frames arrive', async ({ steward, approver }) => {
    stewardContext = steward.context;
    approverContext = approver.context;
    expect(OWNER, 'CAMERA_OWNER or PORTAL_USER names the accountable person').toBeTruthy();
    const key = cameraKey(keyRoot, 'camera');
    camera = key;

    // 1. The gallery form, filled the way a person does; the slug is minted, nobody types it.
    const { page } = steward;
    await openProject(page, PROJECT);
    await openView(page, gallery);
    const card = page.getByRole('button', { name: /camera over mqtt/i })
      .or(page.getByRole('link', { name: /camera over mqtt/i }));
    await expect(card.first(), 'MF-54: the gallery offers "Camera over MQTT"').toBeVisible({ timeout: 20_000 });
    await card.first().click();
    const form = page.getByRole('form').or(page.locator('form')).first();
    await expect(form, 'UI-01: the blueprint opens its generated form').toBeVisible({ timeout: 20_000 });
    await form.getByRole('textbox', { name: /^name/i }).fill(NAME);
    await form.getByRole('combobox', { name: /context space/i }).click();
    await page.getByRole('option', { name: new RegExp(`^${SPACE}\\b`, 'i') }).first().click();
    await form.getByRole('textbox', { name: /^camera/i }).fill(CAMERA);
    await form.getByRole('textbox', { name: /accountable person/i }).fill(OWNER);
    await form.getByRole('textbox', { name: /key id/i }).fill(key.kid);
    await form.getByRole('textbox', { name: /public key x/i }).fill(key.x);
    await form.getByRole('textbox', { name: /public key y/i }).fill(key.y);
    await screenshot(page, '01-form');
    await page.getByRole('button', { name: /save|create|deploy|uložiť|vytvoriť/i }).first().click();
    await expect(
      page.getByText(/pending approval|čaká na schválenie/i).first(),
      'CC-32: saving the form produces a change pending approval',
    ).toBeVisible({ timeout: 30_000 });

    // 2. The approver, a second person, approves that change and it reaches live.
    await openProject(approver.page, PROJECT);
    await openView(approver.page, approvals);
    const pending = approver.page.getByRole('row').or(approver.page.getByRole('article'))
      .filter({ hasText: NAME }).filter({ hasText: /pending|čaká/i }).first();
    await expect(pending, 'UI-23: the camera change is listed for approval').toBeVisible({ timeout: 60_000 });
    await pending.getByRole('button', { name: /review|open|detail|zobraziť/i })
      .or(pending.getByRole('link', { name: /review|open|detail|zobraziť/i })).first().click();
    await screenshot(approver.page, '02-approval');
    await approver.page.getByRole('button', { name: /^approve|schváliť/i }).first().click();
    await expect(
      approver.page.getByText(/^\s*(live|nasadené)\s*$/i).first(),
      'CC-33: the approved change reaches live',
    ).toBeVisible({ timeout: 180_000 });

    // 3. The broker is Live and every edge of this camera's flow is green.
    await openView(page, brokers);
    const card34 = page.getByRole('region', { name: NAME });
    await expect(async () => {
      await page.reload();
      await expect(card34.getByText(/^\s*live\s*$/i).first()).toBeVisible({ timeout: 5_000 });
    }, `MF-54: the broker ${NAME} reaches Live`).toPass({ timeout: 300_000, intervals: [10_000] });
    const address = (await card34.locator('code').filter({ hasText: '.mqtt.' }).first().innerText()).trim();
    expect(address, 'the card shows the broker address').toMatch(new RegExp(`^${NAME}\\.${PROJECT}\\.mqtt\\.[^:]+:\\d+$`));
    broker = address;
    const edges = page.locator('tr[data-edge]').filter({ hasText: NAME });
    await expect(edges.first(), 'the connections table shows this camera\'s flow').toBeVisible({ timeout: 30_000 });
    await expect(page.locator('tr[data-edge="red"]').filter({ hasText: NAME }), 'every edge is green').toHaveCount(0);
    await page.getByRole('heading', { name: /connections/i }).scrollIntoViewIfNeeded();
    await screenshot(page, '03-connections');

    const endpoint = await steward.context.request.get(`/api/v1/projects/${PROJECT}/endpoints/${NAME}`);
    expect(endpoint.status(), 'the Endpoint of the camera exists').toBe(200);
    slug = (await endpoint.json()).spec.slug as string;
    expect(slug, 'EP-02: the Endpoint carries its minted slug').toMatch(/^[a-z2-7]{26,64}$/);

    // 4. The simulator publishes; N is what it says, never a number typed here.
    const run = await publish(key, CAMERA, broker);
    expect(run.code, `jc-camera-sim publish exits 0 (stderr: ${run.stderr.trim()})`).toBe(0);
    const published = run.stdout.match(/published (\d+) of (\d+)/);
    expect(published, 'the simulator prints "published N of M"').toBeTruthy();
    const n = Number(published?.[1]);
    expect(n, 'the broker acknowledged at least one frame').toBeGreaterThan(0);

    // 5. The Endpoint counts exactly N measurements of this camera, and the Device is described.
    await expect.poll(() => measurements(steward.context, slug), {
      message: `the Endpoint counts the ${n} frames the simulator published`,
      timeout: 180_000, intervals: [5_000],
    }).toBe(n);
    const device = await steward.context.request.get(
      `/api/endpoint/${slug}/ngsi-ld/v1/entities/${encodeURIComponent(DEVICE)}`,
    );
    expect(device.status(), 'the camera\'s Device is served').toBe(200);
    const body = await device.json();
    expect(body.controlledProperty, 'the Device names what it measures').toBeTruthy();
    expect(body.deviceState, 'the Device carries its state').toBeTruthy();
  });

  test('a key no ServiceAccount declares is refused at the token endpoint', async () => {
    test.skip(!broker, 'the camera did not reach Live');
    const foreign = cameraKey(keyRoot, 'foreign');
    const run = await publish(foreign, CAMERA, broker);
    expect(run.code, `PF-47: an undeclared key is refused (stderr: ${run.stderr.trim()})`).toBe(2);
    expect(run.stdout, 'nothing is published with an undeclared key').not.toMatch(/published [1-9]/);
  });

  test('a ServiceAccount the broker does not name is refused at CONNECT', async () => {
    test.skip(!broker || !stewardContext || !approverContext, 'the camera did not reach Live');
    const stranger = cameraKey(keyRoot, 'stranger');
    const proposal = await stewardContext!.request.post(`/api/v1/projects/${PROJECT}/serviceaccounts`, {
      headers: await csrfHeaders(stewardContext!),
      data: {
        apiVersion: 'joinedcontext.com/v1alpha1',
        kind: 'ServiceAccount',
        metadata: { name: STRANGER, namespace: PROJECT, title: { en: `Camera ${STRANGER}` } },
        spec: {
          owner: { user: OWNER },
          purpose: `Journey 34: a camera the broker ${NAME} does not name`,
          roles: [{ role: 'space-reader', scope: { contextSpace: SPACE }, types: ['Device'] }],
          credentials: [{
            kind: 'oauth-client', name: 'device-key',
            jwks: { keys: [{ kty: 'EC', crv: 'P-256', kid: stranger.kid, x: stranger.x, y: stranger.y, alg: 'ES256', use: 'sig' }] },
          }],
        },
      },
    });
    expect(proposal.status(), 'the stranger ServiceAccount is proposed').toBe(202);
    const change = await proposal.json();
    if (change.status?.phase === 'PendingApproval') {
      await approveChange(approverContext!, change.metadata.name);
    }
    // The stranger signs in (its key is declared) and the broker refuses it, so the token must work.
    await expect(async () => {
      const response = await stewardContext!.request.get(`/api/v1/projects/${PROJECT}/serviceaccounts/${STRANGER}`);
      expect((await response.json()).status?.phase).toMatch(/live|ready|applied/i);
    }, 'the stranger ServiceAccount is applied').toPass({ timeout: 180_000, intervals: [5_000] });

    const run = await publish(stranger, STRANGER, broker);
    expect(run.code, `ADR-N-048: a ServiceAccount outside the broker's roles is refused (stderr: ${run.stderr.trim()})`).toBe(2);
    expect(run.stderr, 'the refusal comes from the broker, not the token endpoint').toMatch(/not authori[sz]ed|connect/i);
  });

  test('the camera\'s token cannot write through the Endpoint', async ({ request }) => {
    test.skip(!slug || !camera, 'the camera did not reach Live');
    // The camera's own key: its token reads Device in the space and must not write.
    const token = await cameraToken(request, camera!, CAMERA);
    const write = await request.post(`/api/endpoint/${slug}/ngsi-ld/v1/entities`, {
      headers: { authorization: `Bearer ${token}`, 'content-type': 'application/ld+json' },
      data: {
        id: `urn:ngsi-ld:Device:${ORG}:${SPACE}:forged-${suffix}`,
        type: 'Device',
        deviceState: { type: 'Property', value: 'forged' },
        '@context': 'https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.8.jsonld',
      },
    });
    expect(write.status(), 'GW22/PF-45: a reader\'s token writing through the Endpoint is refused').toBe(403);
  });
});

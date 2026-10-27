import { test, expect, openView, openProject } from '../fixtures/portal.js';
import type { APIRequestContext, BrowserContext, Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';

/**
 * Journey 49 (T-4857): the seeded MQTT demo devices over mqtts (MF-54, PL-76, ADR-N-048).
 *
 * The seeds hold two brokers in helsinki (`sensors` on 8883 by SNI, `vehicles` on the dedicated
 * port 18830) and one in banskabystrica (`ovzdusie`), each naming one device ServiceAccount. The
 * approver issues each device a client secret in the Portal (PF-37, shown once, kept in this
 * run's mode-700 temp dir), the device takes a token by client_credentials, connects over mqtts
 * with the token as its password and publishes 10 messages a second apart (the broker's
 * messagesPerSecond is 1), and the space holds the device's entity with the last value within
 * 30 s. The brokers page shows the connected publisher (1440 and 400 px screenshots).
 *
 * Refusals, each with nothing written: no token, an expired token, another project's token, a
 * device's token on another broker of its project, a topic outside the device's own prefix, an
 * oversized message, plain 1883 from outside, and a third broker beyond the allowance (brokers 2).
 *
 * A token never reaches argv: mosquitto_pub reads `-P` from its options file under
 * XDG_CONFIG_HOME in the temp dir. Needs mosquitto_pub on PATH (the sandbox image has it).
 */
const DOMAIN = (process.env.JC_DOMAIN || 'dev.joinedcontext.com').replace(/^https?:\/\//, '');
const IDM = process.env.IDM_URL || `https://idm.${DOMAIN}`;
const REALM = process.env.KEYCLOAK_REALM || 'dev';
const TOKEN_URL = `${IDM}/realms/${REALM}/protocol/openid-connect/token`;
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';
const RUN = String(1000 + crypto.randomInt(9000));

interface Device {
  project: string; account: string; broker: string; space: string; endpoint: string; domain: string;
  host: string; port: number; topic: string; type: string;
  payload: (i: number) => Record<string, number>;
}

const SENSOR: Device = {
  project: 'helsinki', account: 'sensor-kallio', broker: 'sensors', space: 'helsinki', endpoint: 'helsinki-all',
  domain: process.env.JC_ORG_DOMAIN || 'hel.fi', host: `sensors.helsinki.mqtt.${DOMAIN}`, port: 8883,
  topic: 'sensors/helsinki-sensor-kallio/air', type: 'AirQualityObserved',
  payload: (i) => ({ pm10: 10 + i, pm25: 3 + i / 10 }),
};
const TRACKER: Device = {
  project: 'helsinki', account: 'vehicle-demo', broker: 'vehicles', space: 'helsinki', endpoint: 'helsinki-all',
  domain: process.env.JC_ORG_DOMAIN || 'hel.fi', host: `vehicles.helsinki.mqtt.${DOMAIN}`, port: 18830,
  topic: 'vehicles/helsinki-vehicle-demo/position', type: 'Vehicle',
  payload: (i) => ({ lat: 60.17 + i / 1000, lon: 24.94, speed: 8, heading: 90 }),
};
const BB_SENSOR: Device = {
  project: 'banskabystrica', account: 'senzor-namestie', broker: 'ovzdusie', space: 'ovzdusie', endpoint: 'public-air',
  domain: 'banskabystrica.sk', host: `ovzdusie.banskabystrica.mqtt.${DOMAIN}`, port: 8883,
  topic: 'ovzdusie/banskabystrica-senzor-namestie/air', type: 'AirQualityObserved',
  payload: (i) => ({ pm10: 20 + i, pm25: 5 + i / 10 }),
};

const clientId = (d: Device) => `${d.project}-${d.account}`;
const entityId = (d: Device) => `urn:ngsi-ld:${d.type}:${d.domain}:${d.space}:mqtt-${clientId(d)}`;

function screenshotDir(): string {
  const dir = path.join(reportDir, 'journey-49');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

async function csrfHeaders(context: BrowserContext): Promise<Record<string, string>> {
  const csrf = (await context.cookies()).find((cookie) => cookie.name === '__Host-jc_csrf');
  expect(csrf, 'the session carries a CSRF cookie').toBeTruthy();
  return { 'x-csrf-token': csrf?.value ?? '', 'content-type': 'application/json' };
}

/** PF-37: the approver has Keycloak issue the device a new secret; the answer is its only copy. */
async function issueSecret(approver: BrowserContext, d: Device): Promise<string> {
  const response = await approver.request.post(`/api/v1/projects/${d.project}/serviceaccounts/${d.account}/secret`, {
    headers: await csrfHeaders(approver),
  });
  expect(response.status(), `PF-37: the approver issues ${d.project}/${d.account} a secret`).toBe(200);
  const body = await response.json();
  expect(body.client_id).toBe(clientId(d));
  return body.client_secret as string;
}

async function token(request: APIRequestContext, d: Device, secret: string): Promise<string> {
  const response = await request.post(TOKEN_URL, {
    form: { grant_type: 'client_credentials', client_id: clientId(d), client_secret: secret },
  });
  expect(response.status(), `${clientId(d)} signs in with its secret`).toBe(200);
  return (await response.json()).access_token as string;
}

function expiry(jwt: string): number {
  return JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString()).exp as number;
}

interface Run { code: number; out: string }

/**
 * Publishes `lines` (one message each, `gapMs` apart) as `client` over mqtts; `password` goes to
 * the options file, never to argv. Resolves when mosquitto_pub exits.
 */
function publish(root: string, host: string, port: number, client: string, password: string | undefined,
  topic: string, lines: string[], gapMs = 2000): Promise<Run> {
  const home = fs.mkdtempSync(path.join(root, 'mosq-'));
  const args = ['-h', host, '-p', String(port), '--capath', '/etc/ssl/certs', '-i', client, '-q', '1', '-t', topic, '-l'];
  if (password !== undefined) {
    fs.writeFileSync(path.join(home, 'mosquitto_pub'), `-u ${client}\n-P ${password}\n`, { mode: 0o600 });
  }
  return new Promise((resolve) => {
    const child = spawn('mosquitto_pub', args, { env: { ...process.env, XDG_CONFIG_HOME: home } });
    let out = '';
    child.stdout.on('data', (b) => { out += String(b); });
    child.stderr.on('data', (b) => { out += String(b); });
    const timer = setTimeout(() => child.kill('SIGTERM'), 30_000 + lines.length * gapMs);
    child.on('close', (code) => {
      clearTimeout(timer);
      fs.rmSync(home, { recursive: true, force: true });
      resolve({ code: code ?? 1, out });
    });
    let i = 0;
    const next = () => {
      if (i >= lines.length || child.stdin.destroyed) { child.stdin.end(); return; }
      child.stdin.write(`${lines[i++]}\n`);
      setTimeout(next, gapMs);
    };
    next();
  });
}

/** The device's entity through its project's Endpoint, read with the steward's session. */
async function entity(steward: BrowserContext, d: Device, id = entityId(d)): Promise<{ status: number; body?: any }> {
  const ep = await steward.request.get(`/api/v1/projects/${d.project}/endpoints/${d.endpoint}`);
  expect(ep.status(), `${d.project}/${d.endpoint} exists`).toBe(200);
  const slug = (await ep.json()).spec.slug as string;
  const response = await steward.request.get(
    `/api/endpoint/${slug}/ngsi-ld/v1/entities/${encodeURIComponent(id)}?options=keyValues`,
  );
  return { status: response.status(), body: response.status() === 200 ? await response.json() : undefined };
}

function tcpOpen(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port, timeout: 5_000 });
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('timeout', () => { socket.destroy(); resolve(false); });
    socket.once('error', () => resolve(false));
  });
}

const refused = /not authori[sz]ed|refused|bad user name|connection lost|disconnect|error/i;

test.describe('Journey 49: MQTT demo devices over mqtts (T-4857)', () => {
  test.describe.configure({ mode: 'serial', timeout: 900_000 });

  let root = '';
  const secrets = new Map<string, string>();
  const tokens = new Map<string, string>();
  let firstSensorToken = '';
  let stewardContext: BrowserContext | undefined;
  const extraBroker = `t${RUN}`;

  test.beforeAll(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'jc-mqtt-'));
    fs.chmodSync(root, 0o700);
  });

  test.afterAll(async () => {
    if (stewardContext) {
      const response = await stewardContext.request.delete(
        `/api/v1/projects/helsinki/mqttbrokers/${extraBroker}?confirm=${extraBroker}`,
        { headers: await csrfHeaders(stewardContext) },
      ).catch(() => undefined);
      // A third broker the allowance let through would be a Change: its id is in the answer.
      if (response?.status() === 202) console.warn(`journey 49: remove the proposed broker ${extraBroker}`);
    }
    fs.rmSync(root, { recursive: true, force: true });
  });

  test('each device signs in with the secret the approver issued', async ({ approver, steward, request }) => {
    stewardContext = steward.context;
    for (const d of [SENSOR, TRACKER, BB_SENSOR]) {
      secrets.set(clientId(d), await issueSecret(approver.context, d));
      tokens.set(clientId(d), await token(request, d, secrets.get(clientId(d))!));
    }
    firstSensorToken = tokens.get(clientId(SENSOR))!;
  });

  for (const d of [SENSOR, TRACKER, BB_SENSOR]) {
    test(`${d.project}/${d.broker}: 10 messages over mqtts reach the space within 30 s`, async ({ steward, request }) => {
      const pass = await token(request, d, secrets.get(clientId(d))!);
      const lines = Array.from({ length: 10 }, (_, i) => JSON.stringify(d.payload(i)));
      const page = steward.page;
      // The page is read while the device is connected: 10 messages 2 s apart take about 20 s.
      const running = publish(root, d.host, d.port, clientId(d), pass, d.topic, lines);
      await openProject(page, d.project);
      await openView(page, /mqtt brokers|mqtt brokery/i);
      const card = page.locator(`section[aria-labelledby="broker-${d.broker}"]`);
      await expect(card, `the brokers page shows ${d.broker}`).toBeVisible({ timeout: 15_000 });
      await expect(async () => {
        await page.reload();
        await expect(card.locator('[data-measured="yes"]')).toContainText(/[1-9]/, { timeout: 2_000 });
        await expect(card.locator(`[data-account="${d.account}"]`)).toBeVisible({ timeout: 1_000 });
      }, `T-4043: ${d.broker} shows the connected device and its rate`).toPass({ timeout: 15_000, intervals: [1_000] });
      for (const width of [1440, 400]) {
        await page.setViewportSize({ width, height: 900 });
        await card.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(screenshotDir(), `${d.project}-${d.broker}-${width}.png`), fullPage: true });
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      const run = await running;
      expect(run.code, `mosquitto_pub exits 0 (${run.out.trim()})`).toBe(0);
      await expect.poll(async () => {
        const read = await entity(steward.context, d);
        return read.body ? JSON.stringify(read.body) : `status ${read.status}`;
      }, { message: `${entityId(d)} holds the last message`, timeout: 30_000, intervals: [2_000] })
        .toContain(d.type === 'Vehicle' ? String(d.payload(9).lat) : `"pm10":${d.payload(9).pm10}`);
    });
  }

  test('refusals: no token, another project, another broker, outside the prefix, oversized, plain 1883', async ({ steward }) => {
    const before = await entity(steward.context, SENSOR);
    const value = JSON.stringify({ pm10: 999, pm25: 999 });
    const sensor = tokens.get(clientId(SENSOR))!;

    const none = await publish(root, SENSOR.host, 8883, clientId(SENSOR), undefined, SENSOR.topic, [value], 0);
    expect(none.code, `ADR-N-048 §2.3: a CONNECT without a token is refused (${none.out.trim()})`).not.toBe(0);

    const other = await publish(root, SENSOR.host, 8883, clientId(BB_SENSOR), tokens.get(clientId(BB_SENSOR)), SENSOR.topic, [value], 0);
    expect(other.code, `another project's token is refused (${other.out.trim()})`).not.toBe(0);

    const cross = await publish(root, TRACKER.host, TRACKER.port, clientId(SENSOR), sensor,
      `vehicles/${clientId(SENSOR)}/position`, [JSON.stringify(TRACKER.payload(0))], 0);
    expect(cross.code, `a device's token on another broker of its project is refused (${cross.out.trim()})`).not.toBe(0);

    const intruder = `helsinki-intruder-${RUN}`;
    const outside = await publish(root, SENSOR.host, 8883, clientId(SENSOR), sensor, `sensors/${intruder}/air`, [value], 0);
    expect(outside.out + outside.code, 'a publish outside the device\'s own prefix ends the session').toMatch(refused);

    const big = JSON.stringify({ pm10: 999, pm25: 999, pad: 'x'.repeat(2048) });
    const oversized = await publish(root, SENSOR.host, 8883, clientId(SENSOR), sensor, SENSOR.topic, [big], 0);
    expect(oversized.out + oversized.code, 'a message over maxMessageBytes is refused').toMatch(refused);

    expect(await tcpOpen(SENSOR.host, 1883), 'plain MQTT 1883 is closed from outside').toBe(false);

    // Nothing of the above was written: no entity for the intruder, and the sensor keeps its value.
    await new Promise((resolve) => setTimeout(resolve, 15_000));
    expect((await entity(steward.context, SENSOR, `urn:ngsi-ld:AirQualityObserved:${SENSOR.domain}:helsinki:mqtt-${intruder}`)).status).toBe(404);
    const after = await entity(steward.context, SENSOR);
    expect(after.body?.pm10, 'the refused messages wrote nothing').toEqual(before.body?.pm10);
    expect(after.body?.pm10).not.toBe(999);
  });

  test('a third helsinki broker is refused by the allowance (brokers: 2)', async () => {
    test.skip(!stewardContext, 'the steward session did not start');
    const response = await stewardContext!.request.post('/api/v1/projects/helsinki/mqttbrokers', {
      headers: await csrfHeaders(stewardContext!),
      data: {
        apiVersion: 'joinedcontext.com/v1alpha1',
        kind: 'MqttBroker',
        metadata: { name: extraBroker, namespace: 'helsinki', title: { en: `Journey 49 ${extraBroker}` } },
        spec: { roles: [{ name: 'devices', serviceAccounts: ['sensor-kallio'], publish: [`${extraBroker}/\${clientid}/x`] }] },
      },
    });
    expect(response.status(), 'PF-118: the third broker is refused').toBe(403);
    expect(JSON.stringify(await response.json())).toMatch(/may run 2 MQTT brokers/);
  });

  test('an expired token is refused', async () => {
    const left = expiry(firstSensorToken) * 1000 - Date.now();
    test.skip(left > 400_000, `the realm's tokens live ${Math.round(left / 1000)} s; the journey waits at most 400 s`);
    if (left > -5_000) await new Promise((resolve) => setTimeout(resolve, left + 5_000));
    const run = await publish(root, SENSOR.host, 8883, clientId(SENSOR), firstSensorToken, SENSOR.topic,
      [JSON.stringify({ pm10: 999, pm25: 999 })], 0);
    expect(run.code, `an expired token is refused at CONNECT (${run.out.trim()})`).not.toBe(0);
  });
});

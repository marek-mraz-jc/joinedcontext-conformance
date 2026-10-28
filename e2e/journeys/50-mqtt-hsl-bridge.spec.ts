import { test, expect, openView, openProject } from '../fixtures/portal.js';
import type { BrowserContext } from '@playwright/test';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * Journey 50 (T-5883): HSL's live trams through the platform's own MQTT broker on dev.
 *
 * The demo-feeds `hsl-bridge` pod reads HSL's high-frequency positioning feed (trams only) and
 * publishes every frame, its topic under `hsl`, to the helsinki broker `hsl-bridge` over mqtts as
 * the ServiceAccount `hsl-bridge` (a Keycloak token as its password). The pipeline
 * `mqtt-hsl-bridge` reads the broker on its internal listener with the hsl-hfp-vehicles mapping
 * and writes Vehicles into the organization-only space `hsl-mqtt-test`.
 *
 * Checked: over 60 s the test space holds trams (fleetVehicleId `40-<n>`, HSL's tram operator)
 * and its newest position moves on; the brokers page shows the bridge client and its rate
 * (1440 and 400 px screenshots). With JC_HSL_BRIDGE_SECRET_FILE (a mode-600 file holding the
 * secret the deployer stored for the bridge), a publish outside the bridge's tram filter ends the
 * session and writes nothing. The journey never issues the bridge a secret: that would sign the
 * running bridge out.
 */
const DOMAIN = (process.env.JC_DOMAIN || 'dev.joinedcontext.com').replace(/^https?:\/\//, '');
const IDM = process.env.IDM_URL || `https://idm.${DOMAIN}`;
const REALM = process.env.KEYCLOAK_REALM || 'dev';
const TOKEN_URL = `${IDM}/realms/${REALM}/protocol/openid-connect/token`;
const ORG_DOMAIN = process.env.JC_ORG_DOMAIN || 'hel.fi';
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';
const SECRET_FILE = process.env.JC_HSL_BRIDGE_SECRET_FILE || '';
const CLIENT = 'helsinki-hsl-bridge';
const HOST = `hsl-bridge.helsinki.mqtt.${DOMAIN}`;

interface Tram { id: string; fleetVehicleId?: string; location?: { observedAt?: string } }

/** The test space's Vehicles through its Endpoint, read with the steward's session. */
async function trams(steward: BrowserContext): Promise<{ count: number; newest: number; fleet: string[] }> {
  const ep = await steward.request.get('/api/v1/projects/helsinki/endpoints/hsl-mqtt-test');
  expect(ep.status(), 'the seed holds the endpoint hsl-mqtt-test').toBe(200);
  const slug = (await ep.json()).spec.slug as string;
  const response = await steward.request.get(
    `/api/endpoint/${slug}/ngsi-ld/v1/entities?type=Vehicle&count=true&limit=1000&attrs=fleetVehicleId,location&options=concise`,
  );
  expect(response.status(), 'the steward reads the test space').toBe(200);
  const rows = (await response.json()) as Tram[];
  const times = rows.map((r) => Date.parse(r.location?.observedAt ?? '')).filter((t) => !Number.isNaN(t));
  return {
    count: Number(response.headers()['ngsild-results-count'] ?? rows.length),
    newest: times.length ? Math.max(...times) : 0,
    fleet: rows.map((r) => String(r.fleetVehicleId ?? '')),
  };
}

/** One message as the bridge's client over mqtts; the token goes to the options file, never argv. */
function publish(root: string, token: string, topic: string, line: string): Promise<{ code: number; out: string }> {
  const home = fs.mkdtempSync(path.join(root, 'mosq-'));
  fs.writeFileSync(path.join(home, 'mosquitto_pub'), `-u ${CLIENT}\n-P ${token}\n`, { mode: 0o600 });
  return new Promise((resolve) => {
    const child = spawn('mosquitto_pub', ['-h', HOST, '-p', '8883', '--capath', '/etc/ssl/certs', '-i', CLIENT,
      '-q', '1', '-t', topic, '-l'], { env: { ...process.env, XDG_CONFIG_HOME: home } });
    let out = '';
    child.stdout.on('data', (b) => { out += String(b); });
    child.stderr.on('data', (b) => { out += String(b); });
    const timer = setTimeout(() => child.kill('SIGTERM'), 30_000);
    child.on('close', (code) => {
      clearTimeout(timer);
      fs.rmSync(home, { recursive: true, force: true });
      resolve({ code: code ?? 1, out });
    });
    child.stdin.write(`${line}\n`);
    child.stdin.end();
  });
}

test.describe('Journey 50: HSL trams through our MQTT broker (T-5883)', () => {
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  test('the test space holds HSL trams and their newest position moves on over 60 s', async ({ steward }) => {
    const first = await trams(steward.context);
    await new Promise((resolve) => setTimeout(resolve, 60_000));
    const last = await trams(steward.context);
    console.log(`journey 50: ${first.count} -> ${last.count} Vehicles; newest ${new Date(first.newest).toISOString()} -> ${new Date(last.newest).toISOString()}`);
    expect(last.count, 'the bridged trams reach the test space').toBeGreaterThan(0);
    expect(last.newest - first.newest, 'a position at least 30 s newer after 60 s').toBeGreaterThanOrEqual(30_000);
    expect(last.fleet.every((f) => /^40-\d+$/.test(f)), `HSL's trams only (${last.fleet.slice(0, 5).join(', ')})`).toBe(true);
  });

  test('the brokers page shows the bridge client and its rate', async ({ steward }) => {
    const page = steward.page;
    await openProject(page, 'helsinki');
    await openView(page, /mqtt brokers|mqtt brokery/i);
    const card = page.locator('section[aria-labelledby="broker-hsl-bridge"]');
    await expect(card, 'the brokers page shows hsl-bridge').toBeVisible({ timeout: 15_000 });
    await expect(async () => {
      await page.reload();
      await expect(card.locator('[data-measured="yes"]')).toContainText(/[1-9]/, { timeout: 2_000 });
      await expect(card.locator('[data-account="hsl-bridge"]')).toBeVisible({ timeout: 1_000 });
    }, 'T-4043: hsl-bridge shows the connected bridge and its rate').toPass({ timeout: 30_000, intervals: [2_000] });
    const dir = path.join(reportDir, 'journey-50');
    fs.mkdirSync(dir, { recursive: true });
    for (const width of [1440, 400]) {
      await page.setViewportSize({ width, height: 900 });
      await card.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(dir, `helsinki-hsl-bridge-${width}.png`), fullPage: true });
    }
  });

  test('a publish outside the bridge\'s tram filter ends its session and writes nothing', async ({ steward, request }) => {
    test.skip(!SECRET_FILE, 'JC_HSL_BRIDGE_SECRET_FILE names no secret: the journey never issues the bridge one');
    const secret = fs.readFileSync(SECRET_FILE, 'utf8').trim();
    const answer = await request.post(TOKEN_URL, { form: { grant_type: 'client_credentials', client_id: CLIENT, client_secret: secret } });
    expect(answer.status(), `${CLIENT} signs in with its stored secret`).toBe(200);
    const token = (await answer.json()).access_token as string;
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'jc-hsl-bridge-'));
    fs.chmodSync(root, 0o700);
    try {
      // A bus on HSL's own topic shape, under `hsl`: the role holds trams only.
      const vehicle = 99_000 + Math.floor(Math.random() * 900);
      const topic = `hsl/hfp/v2/journey/ongoing/vp/bus/0022/${vehicle}/2550/1/Kamppi/16:00/1040601/5/60;24/19/75/45`;
      const frame = JSON.stringify({ VP: { long: 24.94, lat: 60.17, spd: 1, hdg: 90, route: '2550', tst: new Date().toISOString() } });
      const run = await publish(root, token, topic, frame);
      expect(run.out + run.code, 'ADR-N-048: the per-publish ACL ends the session').toMatch(/not authori[sz]ed|refused|bad user name|connection lost|disconnect|error/i);
      await new Promise((resolve) => setTimeout(resolve, 15_000));
      const ep = await steward.context.request.get('/api/v1/projects/helsinki/endpoints/hsl-mqtt-test');
      const slug = (await ep.json()).spec.slug as string;
      const id = `urn:ngsi-ld:Vehicle:${ORG_DOMAIN}:hsl-mqtt-test:22-${vehicle}`;
      const read = await steward.context.request.get(`/api/endpoint/${slug}/ngsi-ld/v1/entities/${encodeURIComponent(id)}`);
      expect(read.status(), 'the refused frame wrote no Vehicle').toBe(404);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});

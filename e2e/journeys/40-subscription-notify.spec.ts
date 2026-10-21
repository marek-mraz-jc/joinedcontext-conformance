import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from '../fixtures/portal.js';

/**
 * Journey 40 (T-4866, CC-72, DS-16): a steward subscribes a test receiver to a space through the
 * Subscriptions page, the receiver gets a notification, the row shows the deliveries, a receiver
 * that fails shows as failing, the steward pauses one and removes both; a viewer is refused every
 * write in words.
 *
 * The receiver is a public HTTPS request bin, because the broker delivers only to public
 * addresses on 80/443 (context-broker NetworkPolicy, gateway notification_target): set
 * JC_TEST_RECEIVER_URL (where the broker posts) and JC_TEST_RECEIVER_READ_URL (a JSON list of the
 * requests it received, `{ data: [...] }`); unset, the journey makes a fresh webhook.site token.
 * The failing receiver is `https://receiver.invalid/`, which no resolver answers (RFC 6761).
 * The space is `helsinki`, whose city-bike stations change every minute, so no entity is written.
 * With `JC_EVIDENCE_DIR` set it keeps 1440 px and 400 px screenshots of each step there.
 */
const PROJECT = process.env.JC_SUBSCRIPTION_PROJECT ?? 'helsinki';
const SPACE = process.env.JC_SUBSCRIPTION_SPACE ?? 'helsinki';
const TYPE = process.env.JC_SUBSCRIPTION_TYPE ?? 'BikeHireDockingStation';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RUN = `${Date.now().toString(36)}`;
const OK = `t4866-ok-${RUN}`;
const FAILING = `t4866-fail-${RUN}`;

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: join(EVIDENCE, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function receiver(): Promise<{ post: string; read: string }> {
  if (process.env.JC_TEST_RECEIVER_URL && process.env.JC_TEST_RECEIVER_READ_URL) {
    return { post: process.env.JC_TEST_RECEIVER_URL, read: process.env.JC_TEST_RECEIVER_READ_URL };
  }
  const made = await fetch('https://webhook.site/token', { method: 'POST', headers: { accept: 'application/json' } });
  const { uuid } = (await made.json()) as { uuid: string };
  return { post: `https://webhook.site/${uuid}`, read: `https://webhook.site/token/${uuid}/requests?sorting=newest` };
}

async function received(read: string): Promise<number> {
  const answer = await fetch(read, { headers: { accept: 'application/json' } });
  if (!answer.ok) return 0;
  const body = (await answer.json()) as { data?: { method?: string }[] };
  return (body.data ?? []).filter((request) => request.method === 'POST').length;
}

async function openSubscriptions(page: Page): Promise<void> {
  await page.goto(`/projects/${PROJECT}/endpoints?lang=en`);
  await page.getByRole('link', { name: 'Subscriptions', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Subscriptions', level: 1 })).toBeVisible({ timeout: 30_000 });
}

async function propose(page: Page, name: string, address: string): Promise<void> {
  await openSubscriptions(page);
  await page.getByRole('button', { name: 'New Subscription' }).first().click();
  await expect(page).toHaveURL(/\/subscriptions\/new/, { timeout: 20_000 });
  const nameField = page.getByRole('textbox', { name: 'Name', exact: true });
  await expect(nameField).toBeVisible({ timeout: 30_000 });
  await nameField.fill(name);
  const space = page.getByRole('combobox', { name: 'Context Space' });
  await space.selectOption(SPACE).catch(async () => {
    await space.click();
    await page.getByRole('option', { name: SPACE, exact: true }).click();
  });
  await page.getByRole('textbox', { name: 'Address', exact: true }).fill(address);
  await page.getByRole('group', { name: 'Watched entities' }).getByRole('button', { name: 'Add' }).click();
  // The type is searched in the space's data model and picked from its suggestions.
  const type = page.getByRole('combobox', { name: 'Entity type' });
  await type.click();
  await type.pressSequentially(TYPE.slice(0, 8), { delay: 50 });
  await page.getByRole('listbox', { name: 'Choices' }).getByRole('option', { name: new RegExp(`^${TYPE} `) }).first().click();
  await shot(page, `${name.startsWith('t4866-ok') ? 'ok' : 'failing'}-form`);
  // Nothing is proposed without a fresh green verdict: Check first, as a person would.
  await page.getByRole('button', { name: 'Check', exact: true }).click();
  const proposeButton = page.getByRole('button', { name: 'Propose change' });
  await expect(proposeButton).not.toHaveAttribute('aria-disabled', 'true', { timeout: 60_000 });
  const proposed = page.waitForResponse(
    (answer) => answer.request().method() === 'POST' && /\/subscriptions(\?|$)/.test(answer.url()) && !answer.url().includes('dryRun'),
    { timeout: 60_000 },
  );
  await proposeButton.click();
  expect((await proposed).status(), 'the proposal is accepted as a Change').toBe(202);
}

function row(page: Page, name: string) {
  return page.getByRole('row').filter({ has: page.getByRole('link', { name, exact: true }) });
}

/** The Portal shows a reconciler run's outcome within a minute or two; reload until it does. */
async function untilRow(page: Page, name: string, text: RegExp, timeout = 240_000): Promise<void> {
  await expect(async () => {
    await page.reload();
    await expect(row(page, name)).toContainText(text, { timeout: 5_000 });
  }).toPass({ timeout, intervals: [10_000] });
}

async function remove(page: Page, name: string): Promise<void> {
  await openSubscriptions(page);
  const target = row(page, name);
  if ((await target.count()) === 0) return;
  await target.getByRole('button').last().click();
  await page.getByRole('menuitem', { name: 'Remove' }).click();
  await page.getByLabel(`Type ${name} to confirm`).fill(name);
  const proposed = page.waitForResponse(
    (answer) => answer.request().method() === 'DELETE' && answer.url().includes(`/subscriptions/${name}`),
  );
  await page.getByRole('button', { name: 'Propose removal' }).click();
  const change = ((await (await proposed).json()) as { metadata?: { name?: string } }).metadata?.name;
  expect(change, 'a removal is proposed as a Change').toMatch(/^chg-/);
  // A removal is a red-lane change: the steward administers Subscription and confirms it by name
  // (PF-58), as the Approvals page does, so the journey leaves dev as it found it.
  // A second approval while the first merges into the same repository is refused for a moment
  // (seen on dev 2026-10-21: 403, then 202 seconds later), so it is retried.
  await expect(async () => {
  const approved = await page.evaluate(async ([project, id, confirm]) => {
    const csrf = (document.cookie.match(/__Host-jc_csrf=([^;]+)/) ?? [])[1] ?? '';
    const answer = await fetch(`/api/v1/projects/${project}/changes/${id}/approve`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-csrf-token': csrf },
      body: JSON.stringify({ confirm }),
    });
    return answer.status;
  }, [PROJECT, change as string, name]);
  expect(approved).toBe(202);
  }).toPass({ timeout: 90_000, intervals: [5_000] });
}

test.describe('Journey 40: a subscription notifies a receiver (T-4866)', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(900_000);

  test('a steward subscribes, sees the deliveries and a failure, pauses and removes', async ({ browser, baseURL, signIn }) => {
    const user = process.env.PORTAL_STEWARD_USER;
    const pass = process.env.PORTAL_STEWARD_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_STEWARD_USER and PORTAL_STEWARD_PASSWORD name demo.steward');
    const bin = await receiver();
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB', viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string, baseURL);
    try {
      await propose(page, OK, bin.post);
      await propose(page, FAILING, 'https://receiver.invalid/hooks');
      await openSubscriptions(page);

      // The space holds it, the receiver gets a notification, and the row says so.
      await untilRow(page, OK, /Live/);
      const live = Date.now();
      await expect.poll(() => received(bin.read), { timeout: 120_000, intervals: [2_000] }).toBeGreaterThan(0);
      test.info().annotations.push({ type: 'first notification after the space held it', description: `${Date.now() - live} ms` });
      await untilRow(page, OK, /Delivered/);
      await expect(row(page, OK)).toContainText(/Last delivered .+/);
      await shot(page, 'delivered');

      await untilRow(page, FAILING, /Failing/);
      await expect(row(page, FAILING)).toContainText(/did not accept the last notification/);
      await shot(page, 'failing');

      // Pause: the form's "Deliver notifications" off, proposed as a change.
      await row(page, OK).getByRole('button').last().click();
      await page.getByRole('menuitem', { name: /Edit/ }).click();
      await page.getByText('Filters, pace and lifetime').click();
      await page.getByRole('checkbox', { name: 'Deliver notifications' }).uncheck();
      await page.getByRole('button', { name: /Propose/ }).last().click();
      await openSubscriptions(page);
      await untilRow(page, OK, /Paused/);
      await shot(page, 'paused');
    } finally {
      await remove(page, OK);
      await remove(page, FAILING);
    }
    await openSubscriptions(page);
    await expect(async () => {
      await page.reload();
      await expect(row(page, OK)).toHaveCount(0, { timeout: 5_000 });
    }).toPass({ timeout: 240_000, intervals: [10_000] });
    await shot(page, 'removed');
    await context.close();
  });

  test('a viewer reads the subscriptions and is refused every write in words', async ({ browser, baseURL, signIn }) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name demo.viewer');
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB', viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string, baseURL);
    await openSubscriptions(page);
    // The button stays in sight, disabled: the viewer learns the action exists and is not theirs.
    await expect(page.getByRole('button', { name: 'New Subscription' }).first()).toBeDisabled();
    await shot(page, 'viewer-list');

    // The API refuses the write itself: 403, with a hint a person can act on, and nothing stored.
    const refused = await page.evaluate(async ([project, space, type]) => {
      const csrf = (document.cookie.match(/__Host-jc_csrf=([^;]+)/) ?? [])[1] ?? '';
      const answer = await fetch(`/api/v1/projects/${project}/subscriptions`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-csrf-token': csrf },
        body: JSON.stringify({
          apiVersion: 'joinedcontext.com/v1alpha1',
          kind: 'Subscription',
          metadata: { name: 't4866-viewer', namespace: project },
          spec: {
            contextSpaceRef: { kind: 'ContextSpace', name: space },
            entities: [{ type }],
            notification: { endpoint: { uri: 'https://receiver.invalid/' } },
          },
        }),
      });
      return { status: answer.status, body: await answer.text() };
    }, [PROJECT, SPACE, TYPE]);
    expect(refused.status).toBe(403);
    expect(refused.body).toMatch(/Ask a project owner/);
    await page.goto(`/projects/${PROJECT}/subscriptions/new?lang=en`);
    await expect(page.getByText(/Your role lets you look at these, not create one here\./)).toBeVisible({ timeout: 30_000 });
    const proposeButton = page.getByRole('button', { name: 'Propose change' });
    if ((await proposeButton.count()) > 0) await expect(proposeButton.first()).toBeDisabled();
    await shot(page, 'viewer-new');
    await openSubscriptions(page);
    await expect(page.getByRole('link', { name: 't4866-viewer', exact: true })).toHaveCount(0);
    await context.close();
  });
});

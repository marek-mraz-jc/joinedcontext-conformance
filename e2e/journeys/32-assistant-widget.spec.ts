import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * T-3058, T-4138 (API/05 §1.6, AG-114): the public assistant as a visitor meets it: the chat page
 * `JC_ASSISTANT_URL`/d/`JC_ASSISTANT_PUBLIC_ID`, its panel, and the one-line launcher on a city
 * page. The chat route is answered in the browser, so the journey spends no model token and shows
 * what the page does with each event: a streamed answer with its source cards, a data table, a
 * suggested question, a stop, a refusal. The one request that reaches the service is a foreign
 * origin, refused before anything is spent.
 */
const BASE = (process.env.JC_ASSISTANT_URL || 'https://assistant.dev.joinedcontext.com').replace(/\/$/, '');
const PUBLIC_ID = process.env.JC_ASSISTANT_PUBLIC_ID || 'banskabystrica';
/** An origin the deployment lists in `allowedOrigins`: the launcher's host page is served from it. */
const SITE = (process.env.JC_ASSISTANT_SITE || 'https://www.banskabystrica.sk').replace(/\/$/, '');
const PAGE = `${BASE}/d/${PUBLIC_ID}`;
const PANEL = `${PAGE}/widget`;

function stream(events: [string, unknown][]): string {
  return events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
}

const ANSWER = stream([
  ['conversation', { id: '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11' }],
  ['stage', { stage: 'searching', topic: 'zber odpadu' }],
  ['data', {
    endpoint: 'ovzdusie-verejne',
    type: 'AirQualityObserved',
    columns: ['name', 'pm10'],
    rows: [['Námestie SNP', 21]],
    dataset: 'https://data.dev.joinedcontext.com/dataset/ovzdusie',
    download: 'javascript:alert(1)',
  }],
  ['delta', { text: 'Zber odpadu je ' }],
  ['delta', { text: 'v utorok [1].' }],
  ['answer', { text: 'Zber odpadu je **v utorok** [1].' }],
  ['citations', [{
    n: 1, url: 'https://www.banskabystrica.sk/odpad', title: 'Odpad a zber',
    cards: [{ kind: 'contact', name: 'Oddelenie odpadov', phones: ['048/4330 111', 'volajte'], emails: ['odpad@example.org'] }],
  }]],
  ['done', { tokens: 900 }],
]);

/** The chat route answered here; returns what the page asked. */
async function answer(page: Page, body = ANSWER): Promise<() => Record<string, unknown> | undefined> {
  let asked: Record<string, unknown> | undefined;
  await page.route(`${BASE}/api/v1/d/**/chat`, async (route) => {
    asked = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body });
  });
  return () => asked;
}

for (const [name, width, height] of [['phone', 375, 740], ['tablet', 768, 900], ['desktop', 1440, 900]] as const) {
  test(`the chat page answers with its source cards at ${name} width, by keyboard alone (AG-114, T-4138)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const asked = await answer(page);
    const response = await page.goto(PAGE);
    expect(response?.status()).toBe(200);
    expect(response?.headers()['set-cookie']).toBeUndefined();
    expect(response?.headers()['content-security-policy']).toMatch(/frame-ancestors https:\/\//);
    expect(await page.locator('html').getAttribute('lang')).toBe('sk');

    const question = page.getByRole('textbox', { name: 'Napíšte otázku' });
    await question.focus();
    await page.keyboard.type('Kedy je zber odpadu?');
    await page.keyboard.press('Enter');
    const log = page.getByRole('list', { name: 'Asistent' });
    await expect(log.locator('.jc-answer strong')).toHaveText('v utorok');
    const source = log.getByRole('list', { name: 'Zdroje' }).getByRole('link', { name: 'Odpad a zber' });
    await expect(source).toHaveAttribute('href', 'https://www.banskabystrica.sk/odpad');
    await expect(source).toHaveAttribute('target', '_blank');
    await expect(source).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(log.getByRole('cell', { name: 'Námestie SNP' })).toBeVisible();
    await expect(log.getByRole('link', { name: 'Otvoriť dataset' })).toBeVisible();
    // A download address that is not http(s) is never a link.
    await expect(log.getByRole('link', { name: 'Stiahnuť údaje' })).toHaveCount(0);
    await expect(log.getByRole('button', { name: 'Kopírovať' })).toBeVisible();
    // The progress line is gone once the answer streams; the cited page's contact is a card (T-4155).
    await expect(log.getByRole('status')).toHaveCount(0);
    await expect(log.getByRole('link', { name: '048/4330 111' })).toHaveAttribute('href', 'tel:0484330111');
    await expect(log.getByRole('link', { name: 'odpad@example.org' })).toHaveAttribute('href', 'mailto:odpad@example.org');
    await expect(log.getByText('volajte', { exact: true })).toBeVisible();
    await expect(log.getByRole('link', { name: 'volajte' })).toHaveCount(0);
    expect(asked()?.message).toBe('Kedy je zber odpadu?');
    await expect(question).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(axe.violations.map((v) => v.id)).toEqual([]);
  });
}

test('a suggested question asks itself, and a new conversation starts empty (T-4138)', async ({ page }) => {
  const asked = await answer(page);
  await page.goto(PAGE);
  const suggestions = page.getByRole('list', { name: 'Môžete sa opýtať' }).getByRole('button');
  const count = await suggestions.count();
  test.skip(count === 0, `${PUBLIC_ID} sets no suggestions in Slovak`);
  const first = (await suggestions.first().textContent()) ?? '';
  await suggestions.first().click();
  await expect(page.locator('.jc-answer strong')).toHaveText('v utorok');
  expect(asked()?.message).toBe(first.trim());
  await expect(page.getByRole('list', { name: 'Môžete sa opýtať' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Nová konverzácia' }).click();
  await expect(page.locator('.jc-answer')).toHaveCount(0);
  await expect(page.getByRole('list', { name: 'Môžete sa opýtať' }).getByRole('button')).toHaveCount(count);
});

test('the visitor can switch to English, and a stream cut short keeps what it said (T-4138)', async ({ page }) => {
  // The stream ends after its first characters, without an `answer` event.
  await answer(page, stream([['conversation', { id: 'c' }], ['delta', { text: 'Waste is collected' }]]));
  await page.goto(PAGE);
  const english = page.getByRole('button', { name: 'EN', exact: true });
  test.skip((await english.count()) === 0, `${PUBLIC_ID} offers one language only`);
  await english.click();
  await expect(english).toHaveAttribute('aria-pressed', 'true');
  expect(await page.locator('html').getAttribute('lang')).toBe('en');
  await page.getByRole('textbox', { name: 'Type your question' }).fill('When is waste collected?');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.locator('.jc-answer')).toHaveText('Waste is collected');
  await expect(page.getByRole('button', { name: 'Send' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden();
});

test('a progress line says what the assistant searches until the answer starts (T-4155)', async ({ page }) => {
  // The stream stops after its stage, so the line stays where a visitor would read it.
  await answer(page, stream([
    ['conversation', { id: 'c' }],
    ['stage', { stage: 'understanding' }],
    ['stage', { stage: 'searching', topic: 'otváracie hodiny knižnice' }],
  ]));
  await page.goto(PANEL);
  await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Kedy je otvorená knižnica?');
  await page.getByRole('button', { name: 'Odoslať' }).click();
  await expect(page.getByRole('status')).toHaveText('Hľadám v zdrojoch: „otváracie hodiny knižnice“…');
});

test('a refusal reaches the visitor as the sentence the service wrote', async ({ page }) => {
  await answer(page, stream([
    ['conversation', { id: '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11' }],
    ['error', { status: 429, title: 'Budget Spent', detail: 'Dnešné odpovede tohto asistenta sú vyčerpané.' }],
    ['done', { tokens: 0 }],
  ]));
  await page.goto(PANEL);
  await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Ahoj');
  await page.getByRole('button', { name: 'Odoslať' }).click();
  await expect(page.getByRole('alert')).toHaveText('Dnešné odpovede tohto asistenta sú vyčerpané.');
  await expect(page.getByRole('button', { name: 'Odoslať' })).toBeEnabled();
});

test('one script tag on a city page opens the chat, Escape closes it and focus returns (T-4138)', async ({ page }) => {
  await answer(page);
  // The city page, served from an origin the deployment allows, with nothing on it but the tag.
  await page.route(`${SITE}/jc-embed-journey`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: `<!doctype html><html lang="sk"><head><title>Mesto</title></head><body><h1>Mesto</h1>` +
        `<script src="${BASE}/d/${PUBLIC_ID}/embed.js" data-color="#0b5394" defer></script></body></html>`,
    }),
  );
  await page.goto(`${SITE}/jc-embed-journey`);
  const launch = page.getByRole('button', { name: 'Otvoriť asistenta' });
  await launch.focus();
  await page.keyboard.press('Enter');
  const chat = page.frameLocator(`iframe[src^="${BASE}/d/${PUBLIC_ID}/widget"]`);
  await chat.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Kedy je zber odpadu?');
  await chat.getByRole('textbox', { name: 'Napíšte otázku' }).press('Enter');
  await expect(chat.locator('.jc-answer strong')).toHaveText('v utorok');
  await chat.getByRole('textbox', { name: 'Napíšte otázku' }).press('Escape');
  await expect(page.getByRole('button', { name: 'Otvoriť asistenta' })).toBeFocused();
  await expect(page.locator(`iframe[src^="${BASE}/d/${PUBLIC_ID}/widget"]`)).toBeHidden();
});

test('a page on another site is refused before anything is spent (AG-100, AG-114)', async ({ request }) => {
  const refused = await request.post(`${BASE}/api/v1/d/${PUBLIC_ID}/chat`, {
    headers: { Origin: 'https://evil.example', 'Content-Type': 'application/json' },
    data: { message: 'Hello' },
  });
  expect(refused.status()).toBe(403);
});

const CATALOGUE = (process.env.JC_CKAN_URL || 'https://data.dev.joinedcontext.com').replace(/\/$/, '');

test('the catalogue offers its assistant, which opens by keyboard and frames the assistant host (AG-114)', async ({ page }) => {
  await page.goto(`${CATALOGUE}/`);
  const offer = page.locator('details.jc-assistant > summary');
  await offer.focus();
  await page.keyboard.press('Enter');
  const frame = page.locator('details.jc-assistant > iframe');
  await expect(frame).toBeVisible();
  expect(await frame.getAttribute('src')).toMatch(new RegExp(`^${BASE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/d/[a-z0-9-]+/widget$`));
  await expect(page.frameLocator('details.jc-assistant > iframe').getByRole('textbox', { name: /Napíšte otázku|Type your question/ })).toBeVisible();
});

import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * T-3058 (API/05 §1.6, AG-114): the knowledge assistant's widget as a visitor meets it, at phone,
 * tablet and desktop width. The page is the live one at `JC_ASSISTANT_URL`/d/`JC_ASSISTANT_PUBLIC_ID`
 * /widget; the chat route is answered in the browser, so the journey spends no model token and
 * shows what the widget does with each event: an answer with its sources, a script, a refusal.
 * The one request that reaches the service is a foreign origin, refused before anything is spent.
 */
const BASE = (process.env.JC_ASSISTANT_URL || 'https://assistant.dev.joinedcontext.com').replace(/\/$/, '');
const PUBLIC_ID = process.env.JC_ASSISTANT_PUBLIC_ID || 'helsinki-public';
const WIDGET = `${BASE}/d/${PUBLIC_ID}/widget`;

function stream(events: [string, unknown][]): string {
  return events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join('');
}

const ANSWER = stream([
  ['conversation', { id: '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11' }],
  ['tool', { name: 'query_entities', endpoint: 'helsinki-weather', status: 'started' }],
  ['tool', { name: 'query_entities', endpoint: 'helsinki-weather', status: 'done' }],
  ['script', { code: 'return data.length;', output: '12' }],
  ['answer', { text: 'Twelve stations report the road weather [1], see also [2].' }],
  ['citations', [{ n: 1, tool: 'query_entities', endpoint: 'helsinki-weather' }, { n: 2, url: 'https://www.example.org/road-weather' }]],
  ['done', { tokens: 900 }],
]);

for (const [name, width, height] of [['phone', 375, 740], ['tablet', 768, 900], ['desktop', 1440, 900]] as const) {
  test(`the widget answers with its sources at ${name} width, by keyboard alone (AG-114)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    let asked: Record<string, unknown> | undefined;
    await page.route(`${BASE}/api/v1/d/**/chat`, async (route) => {
      asked = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body: ANSWER });
    });
    const response = await page.goto(WIDGET);
    expect(response?.status()).toBe(200);
    expect(response?.headers()['set-cookie']).toBeUndefined();
    expect(response?.headers()['content-security-policy']).toMatch(/frame-ancestors https:\/\//);

    const question = page.getByRole('textbox', { name: 'Your question' });
    await question.focus();
    await page.keyboard.type('What is the road weather?');
    await page.keyboard.press('Enter');
    const log = page.getByRole('list', { name: PUBLIC_ID });
    await expect(log.getByText('Twelve stations report the road weather [1], see also [2].')).toBeVisible();
    await expect(log.getByRole('link', { name: 'https://www.example.org/road-weather' })).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(log.getByText('query_entities · helsinki-weather')).toBeVisible();
    await expect(log.getByText('Script the assistant ran')).toBeVisible();
    expect(asked?.message).toBe('What is the road weather?');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(axe.violations.map((v) => v.id)).toEqual([]);
  });
}

test('a refusal reaches the visitor as the sentence the service wrote', async ({ page }) => {
  await page.route(`${BASE}/api/v1/d/**/chat`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: stream([
        ['conversation', { id: '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11' }],
        ['error', { status: 429, title: 'Budget Spent', detail: "Today's answers for this assistant are used up." }],
        ['done', { tokens: 0 }],
      ]),
    }),
  );
  await page.goto(WIDGET);
  await page.getByRole('textbox', { name: 'Your question' }).fill('Hello');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByText("Today's answers for this assistant are used up.")).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled();
});

test('a page on another site is refused before anything is spent (AG-100, AG-114)', async ({ request }) => {
  const refused = await request.post(`${BASE}/api/v1/d/${PUBLIC_ID}/chat`, {
    headers: { Origin: 'https://evil.example', 'Content-Type': 'application/json' },
    data: { message: 'Hello' },
  });
  expect(refused.status()).toBe(403);
});

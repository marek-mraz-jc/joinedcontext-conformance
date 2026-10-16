# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: e2e/journeys/32-assistant-widget.spec.ts >> one script tag on a city page opens the chat, Escape closes it and focus returns (T-4138)
- Location: e2e/journeys/32-assistant-widget.spec.ts:178:5

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.fill: Test timeout of 30000ms exceeded.
Call log:
  - waiting for locator('iframe[src^="https://assistant.dev.joinedcontext.com/d/banskabystrica/widget"]').contentFrame().getByRole('textbox', { name: 'Napíšte otázku' })

```

# Page snapshot

```yaml
- generic [ref=e1]:
  - heading "Mesto" [level=1] [ref=e2]
  - generic [active]:
    - generic:
      - iframe [ref=e5]:
        
      - button "Zavrieť asistenta" [expanded] [ref=e6] [cursor=pointer]
```

# Test source

```ts
  94  | test('a number over time is drawn as a small chart under its table, hidden from a screen reader (T-4198)', async ({ page }) => {
  95  |   await answer(page, stream([
  96  |     ['conversation', { id: '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11' }],
  97  |     ['data', {
  98  |       endpoint: 'ovzdusie-verejne',
  99  |       type: 'AirQualityObserved',
  100 |       columns: ['dateObserved', 'pm10'],
  101 |       rows: [['2026-10-15T10:00:00Z', 21], ['2026-10-15T09:00:00Z', 18], ['2026-10-15T08:00:00Z', 'n/a']],
  102 |     }],
  103 |     ['answer', { text: 'PM10 je 21.' }],
  104 |     ['done', { tokens: 9 }],
  105 |   ]));
  106 |   await page.goto(PAGE);
  107 |   await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Aké je PM10?');
  108 |   await page.getByRole('textbox', { name: 'Napíšte otázku' }).press('Enter');
  109 |   const log = page.getByRole('list', { name: 'Asistent' });
  110 |   await expect(log.getByRole('cell', { name: '2026-10-15T10:00:00Z' })).toBeVisible();
  111 |   const chart = log.locator('svg.jc-data-chart');
  112 |   await expect(chart).toBeVisible();
  113 |   await expect(chart).toHaveAttribute('aria-hidden', 'true');
  114 |   // The row whose value is no number stays in the table and out of the line.
  115 |   expect((await chart.locator('polyline').getAttribute('points'))?.split(' ')).toHaveLength(2);
  116 |   const axe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  117 |   expect(axe.violations.map((v) => v.id)).toEqual([]);
  118 | });
  119 | 
  120 | test('a suggested question asks itself, and a new conversation starts empty (T-4138)', async ({ page }) => {
  121 |   const asked = await answer(page);
  122 |   await page.goto(PAGE);
  123 |   const suggestions = page.getByRole('list', { name: 'Môžete sa opýtať' }).getByRole('button');
  124 |   const count = await suggestions.count();
  125 |   test.skip(count === 0, `${PUBLIC_ID} sets no suggestions in Slovak`);
  126 |   const first = (await suggestions.first().textContent()) ?? '';
  127 |   await suggestions.first().click();
  128 |   await expect(page.locator('.jc-answer strong')).toHaveText('v utorok');
  129 |   expect(asked()?.message).toBe(first.trim());
  130 |   await expect(page.getByRole('list', { name: 'Môžete sa opýtať' })).toHaveCount(0);
  131 |   await page.getByRole('button', { name: 'Nová konverzácia' }).click();
  132 |   await expect(page.locator('.jc-answer')).toHaveCount(0);
  133 |   await expect(page.getByRole('list', { name: 'Môžete sa opýtať' }).getByRole('button')).toHaveCount(count);
  134 | });
  135 | 
  136 | test('the visitor can switch to English, and a stream cut short keeps what it said (T-4138)', async ({ page }) => {
  137 |   // The stream ends after its first characters, without an `answer` event.
  138 |   await answer(page, stream([['conversation', { id: 'c' }], ['delta', { text: 'Waste is collected' }]]));
  139 |   await page.goto(PAGE);
  140 |   const english = page.getByRole('button', { name: 'EN', exact: true });
  141 |   test.skip((await english.count()) === 0, `${PUBLIC_ID} offers one language only`);
  142 |   await english.click();
  143 |   await expect(english).toHaveAttribute('aria-pressed', 'true');
  144 |   expect(await page.locator('html').getAttribute('lang')).toBe('en');
  145 |   await page.getByRole('textbox', { name: 'Type your question' }).fill('When is waste collected?');
  146 |   await page.getByRole('button', { name: 'Send' }).click();
  147 |   await expect(page.locator('.jc-answer')).toHaveText('Waste is collected');
  148 |   await expect(page.getByRole('button', { name: 'Send' })).toBeVisible();
  149 |   await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden();
  150 | });
  151 | 
  152 | test('a progress line says what the assistant searches until the answer starts (T-4155)', async ({ page }) => {
  153 |   // The stream stops after its stage, so the line stays where a visitor would read it.
  154 |   await answer(page, stream([
  155 |     ['conversation', { id: 'c' }],
  156 |     ['stage', { stage: 'understanding' }],
  157 |     ['stage', { stage: 'searching', topic: 'otváracie hodiny knižnice' }],
  158 |   ]));
  159 |   await page.goto(PANEL);
  160 |   await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Kedy je otvorená knižnica?');
  161 |   await page.getByRole('button', { name: 'Odoslať' }).click();
  162 |   await expect(page.getByRole('status')).toHaveText('Hľadám v zdrojoch: „otváracie hodiny knižnice“…');
  163 | });
  164 | 
  165 | test('a refusal reaches the visitor as the sentence the service wrote', async ({ page }) => {
  166 |   await answer(page, stream([
  167 |     ['conversation', { id: '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11' }],
  168 |     ['error', { status: 429, title: 'Budget Spent', detail: 'Dnešné odpovede tohto asistenta sú vyčerpané.' }],
  169 |     ['done', { tokens: 0 }],
  170 |   ]));
  171 |   await page.goto(PANEL);
  172 |   await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Ahoj');
  173 |   await page.getByRole('button', { name: 'Odoslať' }).click();
  174 |   await expect(page.getByRole('alert')).toHaveText('Dnešné odpovede tohto asistenta sú vyčerpané.');
  175 |   await expect(page.getByRole('button', { name: 'Odoslať' })).toBeEnabled();
  176 | });
  177 | 
  178 | test('one script tag on a city page opens the chat, Escape closes it and focus returns (T-4138)', async ({ page }) => {
  179 |   await answer(page);
  180 |   // The city page, served from an origin the deployment allows, with nothing on it but the tag.
  181 |   await page.route(`${SITE}/jc-embed-journey`, (route) =>
  182 |     route.fulfill({
  183 |       status: 200,
  184 |       contentType: 'text/html',
  185 |       body: `<!doctype html><html lang="sk"><head><title>Mesto</title></head><body><h1>Mesto</h1>` +
  186 |         `<script src="${BASE}/d/${PUBLIC_ID}/embed.js" data-color="#0b5394" defer></script></body></html>`,
  187 |     }),
  188 |   );
  189 |   await page.goto(`${SITE}/jc-embed-journey`);
  190 |   const launch = page.getByRole('button', { name: 'Otvoriť asistenta' });
  191 |   await launch.focus();
  192 |   await page.keyboard.press('Enter');
  193 |   const chat = page.frameLocator(`iframe[src^="${BASE}/d/${PUBLIC_ID}/widget"]`);
> 194 |   await chat.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Kedy je zber odpadu?');
      |                                                               ^ Error: locator.fill: Test timeout of 30000ms exceeded.
  195 |   await chat.getByRole('textbox', { name: 'Napíšte otázku' }).press('Enter');
  196 |   await expect(chat.locator('.jc-answer strong')).toHaveText('v utorok');
  197 |   await chat.getByRole('textbox', { name: 'Napíšte otázku' }).press('Escape');
  198 |   await expect(page.getByRole('button', { name: 'Otvoriť asistenta' })).toBeFocused();
  199 |   await expect(page.locator(`iframe[src^="${BASE}/d/${PUBLIC_ID}/widget"]`)).toBeHidden();
  200 | });
  201 | 
  202 | test('a thumbs down sends its note, a follow-up chip asks itself, and the conversation survives a page change (T-4174)', async ({ page }) => {
  203 |   const id = '6f1c0e9e-3b1a-4d7e-9b51-2c4f8f2a7c11';
  204 |   const asked: Record<string, unknown>[] = [];
  205 |   await page.route(`${BASE}/api/v1/d/**/chat`, async (route) => {
  206 |     asked.push(route.request().postDataJSON() as Record<string, unknown>);
  207 |     await route.fulfill({
  208 |       status: 200,
  209 |       contentType: 'text/event-stream',
  210 |       body: stream([
  211 |         ['conversation', { id }],
  212 |         ['answer', { text: `Odpoveď ${asked.length} [1].` }],
  213 |         ['citations', [{ n: 1, url: 'https://www.banskabystrica.sk/odpad', title: 'Odpad a zber' }]],
  214 |         ['followups', { questions: ['Kde je zberný dvor?', 'Čo patrí do bioodpadu?'] }],
  215 |         ['done', { tokens: 900 }],
  216 |       ]),
  217 |     });
  218 |   });
  219 |   const verdicts: Record<string, unknown>[] = [];
  220 |   await page.route(`${BASE}/api/v1/d/**/feedback`, async (route) => {
  221 |     verdicts.push(route.request().postDataJSON() as Record<string, unknown>);
  222 |     await route.fulfill({ status: 204 });
  223 |   });
  224 |   await page.goto(PANEL);
  225 |   await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('Kedy je zber odpadu?');
  226 |   await page.getByRole('button', { name: 'Odoslať' }).click();
  227 |   await expect(page.locator('.jc-answer').first()).toHaveText('Odpoveď 1 1.');
  228 | 
  229 |   const chips = page.getByRole('list', { name: 'Ďalšie otázky' }).getByRole('button');
  230 |   await expect(chips).toHaveText(['Kde je zberný dvor?', 'Čo patrí do bioodpadu?']);
  231 |   await chips.first().click();
  232 |   await expect(page.locator('.jc-answer')).toHaveCount(2);
  233 |   expect(asked[1].message).toBe('Kde je zberný dvor?');
  234 |   expect(asked[1].conversation).toBe(id);
  235 |   // One list of chips at a time: the latest answer's.
  236 |   await expect(page.getByRole('list', { name: 'Ďalšie otázky' })).toHaveCount(1);
  237 | 
  238 |   await page.getByRole('button', { name: 'Neužitočná odpoveď' }).last().click();
  239 |   await page.getByRole('textbox', { name: 'Čo chýbalo? (nepovinné)' }).fill('Chýbala adresa zberného dvora.');
  240 |   await page.locator('form.jc-note').getByRole('button', { name: 'Odoslať' }).click();
  241 |   await expect(page.getByRole('status').filter({ hasText: 'Ďakujeme za hodnotenie.' })).toBeVisible();
  242 |   expect(verdicts).toEqual([{ conversation: id, turn: 2, verdict: 'down', note: 'Chýbala adresa zberného dvora.' }]);
  243 | 
  244 |   // A page change on the host site: the panel loads again and shows the open conversation.
  245 |   await page.reload();
  246 |   await expect(page.locator('.jc-answer')).toHaveText(['Odpoveď 1 1.', 'Odpoveď 2 1.']);
  247 |   await page.getByRole('textbox', { name: 'Napíšte otázku' }).fill('A v sobotu?');
  248 |   await page.getByRole('button', { name: 'Odoslať' }).click();
  249 |   await expect(page.locator('.jc-answer')).toHaveCount(3);
  250 |   expect(asked[2].conversation).toBe(id);
  251 |   // "Nová konverzácia" forgets it, across a reload too.
  252 |   await page.getByRole('button', { name: 'Nová konverzácia' }).click();
  253 |   await page.reload();
  254 |   await expect(page.locator('.jc-answer')).toHaveCount(0);
  255 |   expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  256 | });
  257 | 
  258 | test('a page on another site is refused before anything is spent (AG-100, AG-114)', async ({ request }) => {
  259 |   const refused = await request.post(`${BASE}/api/v1/d/${PUBLIC_ID}/chat`, {
  260 |     headers: { Origin: 'https://evil.example', 'Content-Type': 'application/json' },
  261 |     data: { message: 'Hello' },
  262 |   });
  263 |   expect(refused.status()).toBe(403);
  264 | });
  265 | 
  266 | const CATALOGUE = (process.env.JC_CKAN_URL || 'https://data.dev.joinedcontext.com').replace(/\/$/, '');
  267 | 
  268 | test('the catalogue offers its assistant, which opens by keyboard and frames the assistant host (AG-114)', async ({ page }) => {
  269 |   await page.goto(`${CATALOGUE}/`);
  270 |   const offer = page.locator('details.jc-assistant > summary');
  271 |   await offer.focus();
  272 |   await page.keyboard.press('Enter');
  273 |   const frame = page.locator('details.jc-assistant > iframe');
  274 |   await expect(frame).toBeVisible();
  275 |   expect(await frame.getAttribute('src')).toMatch(new RegExp(`^${BASE.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/d/[a-z0-9-]+/widget$`));
  276 |   await expect(page.frameLocator('details.jc-assistant > iframe').getByRole('textbox', { name: /Napíšte otázku|Type your question/ })).toBeVisible();
  277 | });
  278 | 
  279 | test('on a phone the open assistant is the whole screen and the closed one covers no last line (T-4204, AG-114)', async ({ page }) => {
  280 |   await page.setViewportSize({ width: 375, height: 740 });
  281 |   await page.goto(`${CATALOGUE}/`);
  282 |   const offer = page.locator('details.jc-assistant > summary');
  283 | 
  284 |   // Closed: scrolled to the end, the page's last link is the element under its own centre.
  285 |   await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  286 |   const last = page.locator('footer a:visible').last();
  287 |   const box = await last.boundingBox();
  288 |   expect(box).not.toBeNull();
  289 |   const hit = await page.evaluate(
  290 |     ([x, y]) => (document.elementFromPoint(x, y) as HTMLElement | null)?.closest('a')?.outerHTML ?? null,
  291 |     [box!.x + box!.width / 2, box!.y + box!.height / 2],
  292 |   );
  293 |   expect(hit).toBe(await last.evaluate((a) => a.outerHTML));
  294 | 
```
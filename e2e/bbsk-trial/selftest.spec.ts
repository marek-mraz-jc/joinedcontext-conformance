import { test, expect } from '@playwright/test';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ask, events, html, judge, markdown, openAssistant, picture, sources, tapChat, writeReport, RED, type Row } from './trial.js';

/**
 * Journey 51's own proof (T-5927), offline: the same code that walks the live demos drives a stub
 * site and a stub assistant (page.route, no network), and a budget refusal, an "I do not know"
 * answer and an answer that never settles each come out as a red row, a sourced answer as green.
 */
const SITE = 'https://site.stub.test';
const ASSISTANT = 'https://assistant.stub.test';

const PAGE = `<!doctype html><meta charset="utf-8"><title>stub site</title><main><button class="hero__ask">Opýtajte sa</button></main>
<div id="jc-assistant" hidden><iframe src="${ASSISTANT}/d/stub/widget" style="position:fixed;right:0;top:0;width:420px;height:100vh;border:0"></iframe></div>
<script>
const panel = document.getElementById('jc-assistant');
if (sessionStorage.getItem('open')) panel.hidden = false;
document.querySelector('.hero__ask').onclick = () => { panel.hidden = false; sessionStorage.setItem('open', '1'); };
addEventListener('message', (e) => { if (e.data && e.data.open) location.href = e.data.open; });
</script>`;

// The widget's contract the walk reads: #jc-chat, its textarea and submit button (hidden while an
// answer streams), .jc-log of .jc-bot turns, .jc-error on a failed one.
const WIDGET = `<!doctype html><meta charset="utf-8"><div id="jc-chat"><ol class="jc-log"></ol>
<form><textarea></textarea><button type="submit">Send</button></form></div>
<script>
const form = document.querySelector('form'), area = form.querySelector('textarea'), send = form.querySelector('button'), log = document.querySelector('.jc-log');
const kept = sessionStorage.getItem('turn');
if (kept) { const li = document.createElement('li'); li.className = 'jc-bot'; li.innerHTML = kept; log.appendChild(li); }
area.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); form.requestSubmit(); } });
form.addEventListener('submit', async (e) => {
  e.preventDefault(); send.hidden = true;
  const li = document.createElement('li'); li.className = 'jc-bot'; log.appendChild(li);
  const r = await fetch('/api/v1/d/stub/chat', { method: 'POST', body: JSON.stringify({ message: area.value }) });
  const text = await r.text();
  if (!r.ok) { li.classList.add('jc-error'); li.textContent = JSON.parse(text).title; }
  else { const m = /event: answer\\ndata: (.*)/.exec(text); li.innerHTML = '<div class="jc-answer"></div>'; li.firstChild.textContent = m ? JSON.parse(m[1]).text : ''; }
  // The real assistant may open a page for the visitor: the host navigates, this frame reloads with the turn kept.
  if (/opens a page/.test(li.textContent)) { li.insertAdjacentHTML('beforeend', '<a href="https://data.stub.test/x">x</a>'); sessionStorage.setItem('turn', li.innerHTML); parent.postMessage({ open: '${SITE}/dataset/x' }, '*'); return; }
  send.hidden = false;
});
</script>`;

const sse = (answer: string, cited: object[]) =>
  `event: answer\ndata: ${JSON.stringify({ text: answer })}\n\nevent: citations\ndata: ${JSON.stringify(cited)}\n\nevent: done\ndata: {}\n\n`;

type Reply = { status: number; body: string; type: string } | 'never';

async function walk(browser: import('@playwright/test').Browser, reply: Reply, limitMs = 15_000) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await tapChat(context);
  await context.route(`${SITE}/**`, (r) => r.fulfill({ contentType: 'text/html', body: PAGE }));
  await context.route(`${ASSISTANT}/d/stub/widget`, (r) => r.fulfill({ contentType: 'text/html', body: WIDGET }));
  await context.route(`${ASSISTANT}/api/v1/d/stub/chat`, (r) => (reply === 'never' ? undefined : r.fulfill({ status: reply.status, contentType: reply.type, body: reply.body })));
  const page = await context.newPage();
  await page.goto(`${SITE}/`);
  const { seen, frame } = await ask(page, await openAssistant(page), 'Aká je kvalita ovzdušia v Banskej Bystrici?', limitMs);
  return { context, page, frame, seen };
}

function row(id: string, seen: ReturnType<typeof judge> & { latencyMs: number | null; sources: string[]; answer: string }): Row {
  return { id, site: 'stub', from: 'selftest', q: 'Aká je kvalita ovzdušia v Banskej Bystrici?', verdict: seen.verdict, cause: seen.cause, latencyMs: seen.latencyMs, sources: seen.sources, answer: seen.answer, shots: [] };
}

test('a sourced answer is a green row; a budget refusal, a "do not know" and a timeout are red rows', async ({ browser }) => {
  test.setTimeout(90_000);
  const rows: Row[] = [];

  const good = await walk(browser, { status: 200, type: 'text/event-stream', body: sse('PM10 je 21 µg/m³ (stanica Štefánikovo nábrežie).', [{ endpoint: 'bbsk-ovzdusie' }]) });
  expect(judge(good.seen)).toEqual({ verdict: 'answered', cause: '' });
  const dir = mkdtempSync(join(tmpdir(), 't5927-'));
  await picture(good.page, good.frame, 400, join(dir, 'good-400.png'));
  rows.push(row('good', { ...judge(good.seen), latencyMs: good.seen.settledMs, sources: sources(good.seen.citations), answer: good.seen.answer }));
  await good.context.close();

  const budget = await walk(browser, { status: 429, type: 'application/problem+json', body: JSON.stringify({ title: 'Daily budget used', status: 429 }) });
  expect(budget.seen.shownError).toBe('Daily budget used');
  expect(judge(budget.seen).verdict).toBe('error');
  expect(judge(budget.seen).cause).toMatch(/^daily budget \(429\)/);
  rows.push(row('budget', { ...judge(budget.seen), latencyMs: budget.seen.settledMs, sources: [], answer: '' }));
  await budget.context.close();

  const unknown = await walk(browser, { status: 200, type: 'text/event-stream', body: sse('Na túto otázku neviem odpovedať.', []) });
  expect(judge(unknown.seen)).toEqual({ verdict: 'no answer', cause: 'says it does not know, cites nothing' });
  rows.push(row('unknown', { ...judge(unknown.seen), latencyMs: unknown.seen.settledMs, sources: [], answer: unknown.seen.answer }));
  await unknown.context.close();

  const slow = await walk(browser, 'never', 3_000);
  expect(slow.seen.timedOut).toBe(true);
  expect(judge(slow.seen)).toEqual({ verdict: 'error', cause: 'timeout' });
  rows.push(row('timeout', { ...judge(slow.seen), latencyMs: null, sources: [], answer: '' }));
  await slow.context.close();

  const moved = await walk(browser, { status: 200, type: 'text/event-stream', body: sse('Dataset Ovzdušie opens a page for you.', []) });
  expect(moved.page.url()).toBe(`${SITE}/dataset/x`);
  expect(moved.seen.navigated).toBe(`${SITE}/dataset/x`);
  expect(moved.seen.answer).toBe('Dataset Ovzdušie opens a page for you.');
  expect(sources(moved.seen.citations)).toEqual(['https://data.stub.test/x']);
  expect(judge(moved.seen).verdict).toBe('answered');
  await picture(moved.page, moved.frame, 1440, join(dir, 'moved-1440.png'));
  await moved.context.close();

  writeReport(dir, rows);
  const report = readFileSync(join(dir, 'report.html'), 'utf8');
  expect(report.match(/<tr class="red">/g)?.length).toBe(3);
  expect(report.match(/<tr class="green">/g)?.length).toBe(1);
  expect(markdown(rows).match(/\*\*RED\*\*/g)?.length).toBe(3);
  expect(rows.filter((r) => RED.has(r.verdict)).map((r) => r.id)).toEqual(['budget', 'unknown', 'timeout']);
});

test('an error event inside a 200 stream is red, and HTML in an answer is escaped in the report', () => {
  const stream = 'event: error\ndata: {"status":504,"title":"Gateway Timeout","detail":"the data read took too long"}\n\n';
  expect(events(stream)).toEqual([['error', { status: 504, title: 'Gateway Timeout', detail: 'the data read took too long' }]]);
  const seen = { answer: '', citations: [], status: 200, problem: { status: 504, title: 'Gateway Timeout' }, shownError: null, timedOut: false, settledMs: 900 };
  expect(judge(seen)).toEqual({ verdict: 'error', cause: '504: Gateway Timeout' });
  const limit = { ...seen, status: 429, problem: { status: 429, title: 'Visitor Limit Reached' } };
  expect(judge(limit).cause).toMatch(/^per-visitor limit \(429\)/);
  const absent = { ...seen, status: 200, problem: null, answer: 'Zverejnené dáta neobsahujú informácie o lekárňach v Brezne.' };
  expect(judge(absent)).toEqual({ verdict: 'no answer', cause: 'says it does not know, cites nothing' });
  const out = html([{ id: 'x', site: 's', from: 'f', q: '<script>x</script>', verdict: 'answered', cause: '', latencyMs: 1, sources: [], answer: '<img src=x>', shots: [] }]);
  expect(out).not.toContain('<script>x');
  expect(out).not.toContain('<img src=x>');
});

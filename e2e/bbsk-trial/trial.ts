/**
 * The BBSK trial walk (T-5927): the region's use-case questions asked on the two public demos the
 * proposal sends the region to, as a visitor asks them: the site opened, its docked assistant
 * opened, the question typed, the whole conversation pictured at 1440 and 400 px. Journey 51
 * drives the live sites; selftest.spec.ts drives the same code against a stub assistant, so a
 * refusal and a timeout are proven to come out as red rows.
 *
 * A row's verdict from the browser alone:
 *   - `error`: the assistant answered with an error (an `error` event, an HTTP refusal such as the
 *     daily budget's 429, the widget's red line) or did not settle within the time limit;
 *   - `no answer`: an empty answer, or one that says it does not know and cites nothing;
 *   - `answered`: text with or without sources. Whether its facts hold is the fact check, written
 *     by the reviewer into the run's report as `ok` or `wrong` (one NGSI-LD read per claim).
 * Red is `error`, `no answer` and `wrong`.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { BrowserContext, Frame, Page } from '@playwright/test';

export type Verdict = 'answered' | 'no answer' | 'error' | 'ok' | 'wrong';

export interface Question {
  id: string;
  /** Where the question comes from: `UC1`..`UC10` or `tile` (proposal page 2). */
  from: string;
  q: string;
}

export interface Citation {
  url?: string;
  endpoint?: string;
  title?: string;
}

/** What the browser saw of one question. */
export interface Observed {
  answer: string;
  citations: Citation[];
  /** HTTP status of the chat POST, null when it never answered. */
  status: number | null;
  /** The `error` event or the problem body of a refused POST. */
  problem: { status?: number; title?: string; detail?: string } | null;
  /** The widget's own red line (`.jc-error`), as the visitor reads it. */
  shownError: string | null;
  timedOut: boolean;
  /** The page the assistant opened for the visitor while it answered, when it did. */
  navigated?: string | null;
  /** From Enter to the send button back; null on a timeout. */
  settledMs: number | null;
}

export interface Row {
  id: string;
  site: string;
  from: string;
  q: string;
  verdict: Verdict;
  cause: string;
  latencyMs: number | null;
  sources: string[];
  answer: string;
  shots: string[];
}

export const RED: ReadonlySet<Verdict> = new Set(['error', 'no answer', 'wrong']);

// "I do not know" in Slovak, Czech and English, as the assistant words an answer without data.
const NO_ANSWER =
  /neviem|neobsahuj|neuvádzaj|nepokrýva|nemám (k dispozícii )?(údaje|informácie|dáta)|nenašiel|nenašla|nenašli|nie sú k dispozícii|nie je k dispozícii|nedokážem|nemôžem odpovedať|nevím|nemám data|I (do not|don't) (know|have)|no (data|information) (is )?available/i;
const BUDGET = /budget|rozpočet|quota|kvót/i;
const VISITOR = /visitor limit|položili veľa otázok/i;

/** The verdict and its cause from what the browser saw. */
export function judge(o: Observed): { verdict: Verdict; cause: string } {
  if (o.timedOut) return { verdict: 'error', cause: 'timeout' };
  const said = [o.problem?.title, o.problem?.detail, o.shownError].filter(Boolean).join(' ');
  const status = o.problem?.status ?? (o.status !== null && o.status >= 400 ? o.status : null);
  if (o.problem || o.shownError || status !== null) {
    if (VISITOR.test(said)) return { verdict: 'error', cause: `per-visitor limit (${status ?? 'no status'}): ${said}`.trim() };
    if (status === 429 || BUDGET.test(said)) return { verdict: 'error', cause: `daily budget (${status ?? 'no status'}): ${said}`.trim() };
    return { verdict: 'error', cause: `${status ?? 'no status'}: ${said || 'no detail'}` };
  }
  const text = o.answer.trim();
  if (!text) return { verdict: 'no answer', cause: 'empty answer' };
  if (NO_ANSWER.test(text) && o.citations.length === 0) return { verdict: 'no answer', cause: 'says it does not know, cites nothing' };
  return { verdict: 'answered', cause: o.citations.length ? '' : 'no source cited' };
}

/** (event, data) pairs of a text/event-stream body. */
export function events(stream: string): [string, unknown][] {
  const out: [string, unknown][] = [];
  let name = 'message';
  let data: string[] = [];
  const flush = () => {
    if (data.length) {
      try {
        out.push([name, JSON.parse(data.join('\n'))]);
      } catch {
        out.push([name, data.join('\n')]);
      }
    }
    name = 'message';
    data = [];
  };
  for (const line of stream.split(/\r?\n/)) {
    if (line === '') flush();
    else if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
  }
  flush();
  return out;
}

/** The answer, citations and error event of a chat stream, or the problem of a refused POST. */
export function read(status: number | null, body: string): Pick<Observed, 'answer' | 'citations' | 'problem'> {
  if (status !== null && status >= 400) {
    let problem: Observed['problem'] = { status };
    try {
      problem = { status, ...(JSON.parse(body) as object) };
    } catch {
      problem = { status, detail: body.slice(0, 300) };
    }
    return { answer: '', citations: [], problem };
  }
  const all = events(body);
  const final = all.filter(([n]) => n === 'answer').map(([, d]) => String((d as { text?: string })?.text ?? ''));
  const answer = final.length ? final[final.length - 1] : all.filter(([n]) => n === 'delta').map(([, d]) => String((d as { text?: string })?.text ?? '')).join('');
  const cited = all.find(([n]) => n === 'citations')?.[1];
  const error = all.find(([n]) => n === 'error')?.[1] as Observed['problem'] | undefined;
  return { answer, citations: Array.isArray(cited) ? (cited as Citation[]) : [], problem: error ?? null };
}

export function sources(citations: Citation[]): string[] {
  return citations.map((c) => c.url || (c.endpoint ? `endpoint:${c.endpoint}` : c.title || '')).filter(Boolean);
}

/** Copies every chat POST's status and body inside the frame that makes it (the assistant's). */
export async function tapChat(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    const w = window as unknown as { __jcChat?: Promise<{ status: number; body: string }>; fetch: typeof fetch };
    const fetched = w.fetch.bind(window);
    w.fetch = async (...call: Parameters<typeof fetch>) => {
      const url = String(call[0] instanceof Request ? call[0].url : call[0]);
      if (!/\/api\/v1\/d\/[^/]+\/chat$/.test(url.split('?')[0])) return fetched(...call);
      const response = await fetched(...call);
      if (!response.body) {
        w.__jcChat = Promise.resolve({ status: response.status, body: '' });
        return response;
      }
      const [mine, copy] = response.body.tee();
      w.__jcChat = new Response(copy).text().then((body) => ({ status: response.status, body }), () => ({ status: response.status, body: '' }));
      return new Response(mine, { status: response.status, statusText: response.statusText, headers: response.headers });
    };
  });
}

/** The assistant's frame, its panel opened first when it is closed (the hero button on the BBSK web, the docked panel on the portal). */
export async function openAssistant(page: Page): Promise<Frame> {
  const iframe = page.locator('#jc-assistant iframe').first();
  if (!(await iframe.isVisible())) {
    const hero = page.locator('.hero__ask');
    if (await hero.count()) await hero.first().click();
    else await page.locator('#jc-assistant summary').first().click();
  }
  await iframe.waitFor({ state: 'visible', timeout: 20_000 });
  const frame = await (await iframe.elementHandle())?.contentFrame();
  if (!frame) throw new Error('the assistant frame did not load');
  await frame.locator('#jc-chat textarea, .jc-chat textarea').last().waitFor({ state: 'visible', timeout: 30_000 });
  return frame;
}

// Settled: the answer's turn is on the page and the send button, hidden while it streams, is back.
function settledIn(frame: Frame, before: number, timeout: number): Promise<void> {
  return frame
    .waitForFunction(
      (n) => {
        const send = Array.from(document.querySelectorAll('#jc-chat button[type="submit"], .jc-chat button[type="submit"]')).pop() as HTMLElement | undefined;
        return document.querySelectorAll('.jc-bot').length > n && !!send && send.offsetParent !== null && !send.hidden;
      },
      before,
      { timeout, polling: 200 },
    )
    .then(() => undefined);
}

/**
 * Types the question in the assistant and waits until its answer settles or `limitMs` passes. The
 * portal's assistant may open a page for the visitor while it answers ("Otvoril som dataset"): the
 * host page navigates, the panel comes back with the conversation restored, and the walk follows
 * it there, reading that answer from the page since its stream went with the old frame.
 */
export async function ask(page: Page, start: Frame, question: string, limitMs: number): Promise<{ seen: Observed; frame: Frame }> {
  let frame = start;
  const input = frame.locator('#jc-chat textarea, .jc-chat textarea').last();
  await input.fill(question);
  const before = await frame.locator('.jc-bot').count();
  const started = Date.now();
  await input.press('Enter');
  let settled = false;
  let navigated: string | null = null;
  while (!settled && Date.now() - started < limitMs) {
    try {
      await settledIn(frame, before, limitMs - (Date.now() - started));
      settled = true;
    } catch (err) {
      if (!frame.isDetached()) break;
      navigated = page.url();
      await page.waitForLoadState('load').catch(() => {});
      frame = await openAssistant(page).catch(() => frame);
      if (frame.isDetached()) throw err;
    }
  }
  const settledMs = settled ? Date.now() - started : null;
  const chat = settled && !navigated
    ? await frame.evaluate(() => (window as unknown as { __jcChat?: Promise<{ status: number; body: string }> }).__jcChat ?? null)
    : null;
  const last = frame.locator('.jc-bot').last();
  const shownError = (await last.count()) && (await last.evaluate((el) => el.classList.contains('jc-error')))
    ? (await last.innerText()).trim()
    : null;
  let got: Pick<Observed, 'answer' | 'citations' | 'problem'> = chat ? read(chat.status, chat.body) : { answer: '', citations: [], problem: null };
  if (settled && navigated && !shownError) {
    const answer = (await last.locator('.jc-answer').last().innerText().catch(() => '')).trim();
    const links = await last.locator('a[href^="http"]').evaluateAll((all) => all.map((a) => (a as HTMLAnchorElement).href));
    got = { answer, citations: [...new Set(links)].map((url) => ({ url })), problem: null };
  }
  return { seen: { ...got, status: chat?.status ?? null, shownError, timedOut: !settled, settledMs, navigated }, frame };
}

/** The whole conversation pictured: the viewport grown until the assistant's log fits. */
export async function picture(page: Page, frame: Frame, width: number, file: string): Promise<void> {
  await page.setViewportSize({ width, height: width > 600 ? 900 : 860 });
  await page.waitForTimeout(600);
  const need = await frame.evaluate(() => {
    const log = document.querySelector('.jc-log');
    const view = document.documentElement.clientHeight;
    return log ? view - log.clientHeight + log.scrollHeight : view;
  });
  const box = await page.locator('#jc-assistant iframe').first().boundingBox();
  const vh = width > 600 ? 900 : 860;
  const height = Math.min(Math.max(vh, Math.ceil(vh - (box?.height ?? vh) + need)), 12_000);
  await page.setViewportSize({ width, height });
  await page.waitForTimeout(600);
  await page.screenshot({ path: file });
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string);
const cell = (s: string) => s.replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim();
const secs = (ms: number | null) => (ms === null ? '-' : `${(ms / 1000).toFixed(1)} s`);

export function markdown(rows: Row[]): string {
  const head = '| # | site | from | question | verdict | cause | latency | sources | shots |\n|---|---|---|---|---|---|---|---|---|\n';
  return head + rows
    .map((r) => `| ${r.id} | ${r.site} | ${r.from} | ${cell(r.q)} | ${RED.has(r.verdict) ? '**RED** ' : ''}${r.verdict} | ${cell(r.cause) || '-'} | ${secs(r.latencyMs)} | ${r.sources.length} | ${r.shots.join(', ')} |`)
    .join('\n') + '\n';
}

export function html(rows: Row[]): string {
  const body = rows
    .map((r) => `<tr class="${RED.has(r.verdict) ? 'red' : 'green'}"><td>${esc(r.id)}</td><td>${esc(r.site)}</td><td>${esc(r.q)}</td><td>${esc(r.verdict)}</td><td>${esc(r.cause)}</td><td>${secs(r.latencyMs)}</td><td>${r.sources.map((s) => esc(s)).join('<br>')}</td><td>${esc(r.answer.slice(0, 600))}</td><td>${r.shots.map((s) => `<a href="${esc(s)}">${esc(s)}</a>`).join(' ')}</td></tr>`)
    .join('\n');
  return `<!doctype html><meta charset="utf-8"><title>BBSK trial questions</title>
<style>body{font:14px system-ui;margin:16px}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}tr.red{background:#fde2e2}tr.green{background:#e3f4e6}</style>
<table><thead><tr><th>#</th><th>site</th><th>question</th><th>verdict</th><th>cause</th><th>latency</th><th>sources</th><th>answer</th><th>shots</th></tr></thead><tbody>
${body}
</tbody></table>`;
}

export function writeReport(dir: string, rows: Row[]): void {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'results.json'), JSON.stringify(rows, null, 1));
  writeFileSync(join(dir, 'report.md'), markdown(rows));
  writeFileSync(join(dir, 'report.html'), html(rows));
}

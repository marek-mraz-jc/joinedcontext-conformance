import { test, expect } from '@playwright/test';
import { appendFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { ask, judge, openAssistant, picture, sources, tapChat, writeReport, RED, type Question, type Row } from '../bbsk-trial/trial.js';

/**
 * T-5927: the BBSK trial walk on the two public demos the proposal names, the BBSK web
 * (`JC_BBSK_DEMO_URL`, prod https://bbsk.smartcity.joinedcontext.com) and the open data portal
 * (`JC_OPENDATA_URL`, prod https://opendata.smartcity.joinedcontext.com). Each question of
 * bbsk-trial/questions.json is asked on each site in a fresh anonymous browser, one conversation
 * per question, and its whole conversation pictured at 1440 and 400 px into `JC_EVIDENCE_DIR`
 * with results.json, report.md and report.html (red rows: error, no answer).
 *
 * Every question spends the public assistant's daily budget (BBSK public: 1 USD a day, about 100
 * questions), so the journey runs only with `JC_TRIAL_RUN=1`: once per release, never in a loop.
 * Nothing personal is typed; no cookie or token is sent.
 */
const RUN = process.env.JC_TRIAL_RUN === '1';
const EVIDENCE = process.env.JC_EVIDENCE_DIR || join(process.env.JC_REPORTS_DIR || 'e2e/reports', 'bbsk-trial');
const SITES: Record<string, string> = {
  bbsk: (process.env.JC_BBSK_DEMO_URL || 'https://bbsk.smartcity.joinedcontext.com').replace(/\/$/, ''),
  opendata: (process.env.JC_OPENDATA_URL || 'https://opendata.smartcity.joinedcontext.com').replace(/\/$/, ''),
};
// A data question settles within the service's own 45 s limit plus the stream; 120 s is a timeout.
const LIMIT_MS = Number(process.env.JC_TRIAL_LIMIT_MS || 120_000);
const QUESTIONS: Question[] = JSON.parse(readFileSync(join(__dirname, '..', 'bbsk-trial', 'questions.json'), 'utf8')).questions;
const ROWS = join(EVIDENCE, 'rows.jsonl');

test.describe.configure({ timeout: LIMIT_MS + 90_000 });

test.beforeAll(() => {
  if (RUN) mkdirSync(EVIDENCE, { recursive: true });
});

for (const [site, url] of Object.entries(SITES)) {
  for (const question of QUESTIONS) {
    test(`${site} ${question.id}: ${question.q}`, async ({ browser }) => {
      test.skip(!RUN, 'asks the public assistant (daily budget): set JC_TRIAL_RUN=1 for the one run per release');
      const context = await browser.newContext({ locale: 'sk-SK', viewport: { width: 1440, height: 900 } });
      await tapChat(context);
      const page = await context.newPage();
      try {
        const answered = await page.goto(url);
        expect(answered?.status()).toBe(200);
        const { seen, frame } = await ask(page, await openAssistant(page), question.q, LIMIT_MS);
        const { verdict, cause } = judge(seen);
        const name = `${site}-${question.id}`;
        const shots = [`${name}-1440.png`, `${name}-400.png`];
        await picture(page, frame, 1440, join(EVIDENCE, shots[0]));
        await picture(page, frame, 400, join(EVIDENCE, shots[1]));
        const row: Row = { id: question.id, site, from: question.from, q: question.q, verdict, cause: seen.navigated ? `${cause} (opened ${seen.navigated})`.trim() : cause, latencyMs: seen.settledMs, sources: sources(seen.citations), answer: seen.answer, shots };
        appendFileSync(ROWS, JSON.stringify(row) + '\n');
        expect.soft(RED.has(verdict), `${name}: ${verdict} ${cause}`).toBe(false);
      } finally {
        await context.close();
        // The per-visitor limit counts one address: a pause between questions keeps the walk under it.
        await new Promise((r) => setTimeout(r, 5_000));
      }
    });
  }
}

test.afterAll(() => {
  if (!RUN || !existsSync(ROWS)) return;
  // A restarted worker runs afterAll again: the last row of each site and question wins.
  const rows = new Map<string, Row>();
  for (const line of readFileSync(ROWS, 'utf8').split('\n').filter(Boolean)) {
    const row: Row = JSON.parse(line);
    rows.set(`${row.site}/${row.id}`, row);
  }
  writeReport(EVIDENCE, [...rows.values()]);
});

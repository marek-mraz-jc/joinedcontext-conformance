import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect, signIn } from '../fixtures/portal.js';

/**
 * Journey 41 (T-4869, AG-30, AG-31): a steward adds an uploaded-files source and a website source
 * on the Knowledge sources page, uploads a PDF, watches both get read, finds a sentence of the PDF
 * with "Try the search", asks the project's internal assistant a question only the PDF answers,
 * and removes both sources; a viewer is refused every write in words.
 *
 * The PDF (fixtures/knowledge/kumpula-garden-rules.pdf) describes a garden that does not exist, so
 * no other source answers from it. The website source starts at JC_KNOWLEDGE_WEB_URL (default
 * https://example.com/, one stable page). The model step: while the dev spend guard holds the
 * model (owner), the chat answers "the model did not answer" and the journey records that; the
 * grounded answer and its citation are then proven by the assistant crate's stubbed-model test
 * (platform crates/assistant/tests/chat_tests.rs). Set JC_KNOWLEDGE_EXPECT_MODEL=1 to require a
 * live answer that cites the PDF.
 *
 * Both sources are named t4869-*-<run> and removed in `finally`, so the journey leaves dev as it
 * found it. With JC_EVIDENCE_DIR set it keeps 1440 px and 400 px screenshots of each step there.
 */
const PROJECT = process.env.JC_KNOWLEDGE_PROJECT ?? 'helsinki';
const WEB_URL = process.env.JC_KNOWLEDGE_WEB_URL ?? 'https://example.com/';
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
const RUN = Date.now().toString(36);
const FILES = `t4869-files-${RUN}`;
const WEB = `t4869-web-${RUN}`;
const ASSISTANT = `t4869-ask-${RUN}`;
const EXPECT_MODEL = process.env.JC_KNOWLEDGE_EXPECT_MODEL === '1';
/** JC_KNOWLEDGE_WEB=0 leaves the website step out: before T-4869's Portal half, the form proposed upload limits on every website source and jc-core refused it. */
const WITH_WEB = process.env.JC_KNOWLEDGE_WEB !== '0';
const PDF = join(__dirname, '..', 'fixtures', 'knowledge', 'kumpula-garden-rules.pdf');
const PDF_NAME = 'kumpula-garden-rules.pdf';
/** A word only the PDF holds. */
const PDF_WORD = 'Liljankukka';
const QUESTION = 'When and where does the Kumpula allotment garden committee meet?';

async function shot(page: Page, name: string): Promise<void> {
  if (!EVIDENCE) return;
  mkdirSync(EVIDENCE, { recursive: true });
  for (const width of [1440, 400]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: join(EVIDENCE, `${name}-${width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function openKnowledge(page: Page): Promise<void> {
  await page.goto(`/projects/${PROJECT}/knowledge?lang=en`);
  await expect(page.getByRole('heading', { name: 'Knowledge sources', level: 1 })).toBeVisible({ timeout: 30_000 });
}

function sourceRow(page: Page, name: string) {
  return page.getByRole('table', { name: 'Knowledge sources' }).getByRole('row').filter({ hasText: name });
}

/** Proposes a KnowledgeSource through the page's "Add a source" form; the steward's own approve follows at once. */
async function propose(page: Page, name: string, type: 'Uploaded files' | 'Website'): Promise<void> {
  await openKnowledge(page);
  await page.getByRole('button', { name: 'Add a source' }).first().click();
  await expect(page).toHaveURL(/\/knowledgesources\/new/, { timeout: 20_000 });
  const nameField = page.getByRole('textbox', { name: 'Name', exact: true });
  await expect(nameField).toBeVisible({ timeout: 30_000 });
  await nameField.fill(name);
  await page.getByRole('combobox', { name: 'Type', exact: true }).selectOption({ label: type });
  if (type === 'Website') {
    await page.getByRole('group', { name: 'Start addresses' }).getByRole('button', { name: 'Add' }).click();
    await page.getByRole('group', { name: 'Start addresses' }).getByRole('textbox').last().fill(WEB_URL);
  }
  await page.getByRole('combobox', { name: 'Who may be answered from it' }).selectOption({ label: 'Internal: signed-in staff' });
  await shot(page, `${type === 'Website' ? 'web' : 'files'}-form`);
  await page.getByRole('button', { name: 'Check', exact: true }).click();
  const proposeButton = page.getByRole('button', { name: 'Propose change' });
  await expect(proposeButton).not.toHaveAttribute('aria-disabled', 'true', { timeout: 60_000 });
  const proposed = page.waitForResponse(
    (answer) => answer.request().method() === 'POST' && /\/knowledgesources(\?|$)/.test(answer.url()) && !answer.url().includes('dryRun'),
    { timeout: 60_000 },
  );
  await proposeButton.click();
  expect((await proposed).status(), 'the proposal is accepted as a Change').toBe(202);
}

/** The reconciler applies an approved change within a few minutes; reload until the row shows `text`. */
async function untilRow(page: Page, name: string, text: RegExp, timeout = 600_000): Promise<void> {
  await expect(async () => {
    await openKnowledge(page);
    await expect(sourceRow(page, name)).toContainText(text, { timeout: 5_000 });
  }).toPass({ timeout, intervals: [15_000] });
}

/** Proposes the removal of a manifest by its API and approves it, as the Approvals page does. */
async function remove(page: Page, name: string, plural = 'knowledgesources'): Promise<void> {
  const outcome = await page.evaluate(
    async ([project, source, kind]) => {
      const csrf = (document.cookie.match(/__Host-jc_csrf=([^;]+)/) ?? [])[1] ?? '';
      const headers = { 'content-type': 'application/json', 'x-csrf-token': csrf };
      const gone = await fetch(`/api/v1/projects/${project}/${kind}/${source}`, { method: 'DELETE', headers });
      if (gone.status === 404) return { status: 404, approved: 0 };
      const change = ((await gone.json().catch(() => ({}))) as { metadata?: { name?: string } }).metadata?.name;
      if (!change) return { status: gone.status, approved: 0 };
      const approve = await fetch(`/api/v1/projects/${project}/changes/${change}/approve`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ confirm: source }),
      });
      return { status: gone.status, approved: approve.status };
    },
    [PROJECT, name, plural],
  );
  expect([202, 404], `removing ${name} is proposed: ${JSON.stringify(outcome)}`).toContain(outcome.status);
}

/** An internal assistant that reads only `source`, proposed on the page's "Add an assistant" form. */
async function proposeAssistant(page: Page): Promise<void> {
  // The new source is offered once the Portal lists it; a person reloads the form until then.
  const box = page.getByRole('group', { name: `Sources of ${PROJECT}` }).getByRole('checkbox', { name: new RegExp(`^${FILES}\\b`) });
  await expect(async () => {
    await openKnowledge(page);
    await page.getByRole('button', { name: 'Add an assistant' }).click();
    await expect(page).toHaveURL(/\/assistantdeployments\/new/, { timeout: 20_000 });
    const offered = page.getByRole('group', { name: `Sources of ${PROJECT}` });
    await expect(offered).toBeVisible({ timeout: 20_000 });
    await expect(box, `the sources offered: ${await offered.innerText()}`).toBeVisible({ timeout: 10_000 });
  }).toPass({ timeout: 300_000, intervals: [15_000] });
  const nameField = page.getByRole('textbox', { name: 'Name', exact: true });
  await nameField.fill(ASSISTANT);
  await page.getByRole('textbox', { name: 'Public id' }).fill(ASSISTANT);
  await page.getByRole('combobox', { name: 'Channel' }).selectOption({ label: 'Internal (Portal)' });
  await box.check();
  // The limits said rather than left to the form's defaults (before T-4824 and T-4869 reach dev,
  // the defaults left a token and a rate limit out and Check refused every new assistant).
  const perDay = page.getByRole('spinbutton', { name: 'Tokens a day' });
  if (!(await perDay.isVisible())) await page.getByText('Limits and budget', { exact: true }).click();
  await perDay.fill('200000');
  await page.getByRole('spinbutton', { name: 'Tokens a conversation' }).fill('20000');
  await page.getByRole('spinbutton', { name: 'Questions a minute, everyone' }).fill('60');
  await page.getByRole('spinbutton', { name: 'Questions a minute, one visitor' }).fill('5');
  await shot(page, 'assistant-form');
  await page.getByRole('button', { name: 'Check', exact: true }).click();
  const proposeButton = page.getByRole('button', { name: 'Propose change' });
  await expect(proposeButton).not.toHaveAttribute('aria-disabled', 'true', { timeout: 60_000 });
  const proposed = page.waitForResponse(
    (answer) => answer.request().method() === 'POST' && /\/assistantdeployments(\?|$)/.test(answer.url()) && !answer.url().includes('dryRun'),
    { timeout: 60_000 },
  );
  await proposeButton.click();
  expect((await proposed).status(), 'the assistant is proposed as a Change').toBe(202);
}

/**
 * Asks the journey's assistant `question` and returns what the dialog shows: the answer's sources
 * when the model answered, or the error sentence when it did not.
 */
async function ask(page: Page, question: string, step: string): Promise<{ answered: boolean; sources: string; error: string }> {
  await openKnowledge(page);
  await page.getByRole('button', { name: `Ask ${ASSISTANT}`, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: `Ask ${ASSISTANT}` });
  await dialog.getByRole('textbox', { name: 'Your question' }).fill(question);
  await dialog.getByRole('button', { name: 'Send' }).click();
  const conversation = dialog.getByRole('list', { name: 'Conversation' });
  const answer = conversation.getByRole('list', { name: 'Sources' });
  const failure = conversation.getByRole('alert');
  await expect(answer.or(failure).first(), 'the assistant answers with its sources or says why not').toBeVisible({ timeout: 120_000 });
  await shot(page, step);
  const answered = await answer.isVisible();
  const result = {
    answered,
    sources: answered ? await answer.innerText() : '',
    error: answered ? '' : await failure.innerText(),
  };
  await dialog.getByRole('button', { name: 'Close' }).click();
  return result;
}

/** A viewer reads the sources and is refused every write: the buttons say why, the API refuses with 403. */
async function viewerIsRefused(browser: import('@playwright/test').Browser, baseURL: string | undefined): Promise<void> {
  const user = process.env.PORTAL_VIEWER_USER;
  const pass = process.env.PORTAL_VIEWER_PASSWORD;
  if (!user || !pass) return;
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB', viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  try {
    await page.goto('/');
    await signIn(page, user, pass, baseURL);
    await openKnowledge(page);
    await expect(sourceRow(page, FILES), 'the viewer reads the new source').toBeVisible();
    await expect(page.getByRole('button', { name: 'Add a source' }).first(), 'adding a source is not the viewer\'s').toHaveAttribute('aria-disabled', 'true');
    await shot(page, 'viewer-list');
    await sourceRow(page, FILES).getByRole('link', { name: FILES }).click();
    await expect(page.getByRole('heading', { name: new RegExp(FILES) }).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByLabel('Choose files to upload'), 'the viewer gets no file picker').toHaveCount(0);
    await expect(page.getByRole('button', { name: /Remove kumpula-garden-rules\.pdf/ }), 'removing a file is not the viewer\'s').toHaveAttribute('aria-disabled', 'true');
    await shot(page, 'viewer-source');
    const refused = await page.evaluate(
      async ([project, source]) => {
        const csrf = (document.cookie.match(/__Host-jc_csrf=([^;]+)/) ?? [])[1] ?? '';
        const headers = { 'x-csrf-token': csrf };
        const upload = await fetch(`/api/v1/projects/${project}/knowledge/sources/${source}/files?name=viewer.txt`, {
          method: 'POST',
          headers: { ...headers, 'content-type': 'text/plain' },
          body: 'written by a viewer',
        });
        const gone = await fetch(`/api/v1/projects/${project}/knowledgesources/${source}`, { method: 'DELETE', headers });
        const listed = await fetch(`/api/v1/projects/${project}/knowledge/sources/${source}/files`);
        return { upload: upload.status, remove: gone.status, files: JSON.stringify(await listed.json()) };
      },
      [PROJECT, FILES],
    );
    expect(refused.upload, 'a viewer\'s upload is refused').toBe(403);
    expect(refused.remove, 'a viewer\'s removal is refused').toBe(403);
    expect(refused.files, 'nothing the viewer sent was written').not.toContain('viewer.txt');
  } finally {
    await context.close();
  }
}

test.describe('Journey 41: knowledge sources answer the assistant (T-4869)', () => {
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(1_800_000);

  test('a steward adds a file and a web page, finds them, asks, and removes them', async ({ browser, baseURL }) => {
    const user = process.env.PORTAL_USER;
    const pass = process.env.PORTAL_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_USER and PORTAL_PASSWORD name demo.steward');
    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB', viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    await page.goto('/');
    await signIn(page, user as string, pass as string, baseURL);
    try {
      await openKnowledge(page);
      await shot(page, 'list-before');
      // The stored conversations and their usage, the assistant administrator's page.
      await page.getByRole('link', { name: 'Conversations' }).click();
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 30_000 });
      await expect(page.getByRole('main')).not.toContainText(/could not be read|failed/i, { timeout: 30_000 });
      await shot(page, 'conversations');

      // 1. An uploaded-files source, and the PDF sent to it.
      await propose(page, FILES, 'Uploaded files');
      await untilRow(page, FILES, /Not crawled yet|Crawled/);
      await shot(page, 'files-listed');
      await sourceRow(page, FILES).getByRole('link', { name: FILES }).click();
      await expect(page.getByRole('heading', { name: new RegExp(FILES) }).first()).toBeVisible({ timeout: 30_000 });
      await shot(page, 'files-source-empty');
      // The assistant takes an approved source up within about a minute; until then the page
      // says so and offers no upload, so the person (and the journey) sends the file again.
      await expect(async () => {
        await page.reload();
        await page.getByLabel('Choose files to upload').setInputFiles(PDF, { timeout: 5_000 });
        await expect(page.getByRole('table', { name: 'Files' }).getByRole('row').filter({ hasText: PDF_NAME }), 'the uploaded PDF is listed').toBeVisible({ timeout: 15_000 });
      }).toPass({ timeout: 300_000, intervals: [15_000] });
      await expect(async () => {
        await page.reload();
        await expect(page.getByRole('table', { name: 'Files' }).getByRole('row').filter({ hasText: PDF_NAME })).toContainText(/[1-9]\d* passages|\b[1-9]\d*\b/, { timeout: 5_000 });
      }).toPass({ timeout: 300_000, intervals: [10_000] });
      await shot(page, 'files-indexed');

      // 2. Search inside it, once its passages are embedded (the index status counts them).
      await expect(async () => {
        await page.reload();
        await page.getByRole('searchbox', { name: 'Question or words' }).fill(PDF_WORD);
        await page.getByRole('button', { name: 'Try the search' }).click();
        await expect(page.getByRole('main'), 'the search finds the PDF passage').toContainText(/third Thursday/i, { timeout: 15_000 });
      }).toPass({ timeout: 600_000, intervals: [15_000] });
      await shot(page, 'files-search');

      // 3. A website source, read by its first crawl.
      if (WITH_WEB) {
        await propose(page, WEB, 'Website');
        await untilRow(page, WEB, /Crawled|Last crawl failed/, 900_000);
        await expect(sourceRow(page, WEB), 'the web page is read').toContainText('Crawled');
        await shot(page, 'web-crawled');
        await sourceRow(page, WEB).getByRole('link', { name: WEB }).click();
        await page.getByRole('searchbox', { name: 'Question or words' }).fill('documentation examples');
        await page.getByRole('button', { name: 'Try the search' }).click();
        await expect(page.getByRole('main'), 'the search finds the web page').toContainText(new URL(WEB_URL).hostname, { timeout: 30_000 });
        await shot(page, 'web-search');
      } else {
        test.info().annotations.push({ type: 'skipped', description: 'website step left out (JC_KNOWLEDGE_WEB=0)' });
      }

      // 4. A viewer reads them and is refused every write.
      await viewerIsRefused(browser, baseURL);

      // 5. The assistant answers from the PDF and cites it.
      await proposeAssistant(page);
      await expect(async () => {
        await openKnowledge(page);
        await expect(page.getByRole('button', { name: `Ask ${ASSISTANT}`, exact: true })).toBeVisible({ timeout: 5_000 });
      }).toPass({ timeout: 600_000, intervals: [15_000] });
      const grounded = await ask(page, QUESTION, 'ask-grounded');
      if (grounded.answered) {
        expect(grounded.sources, 'the answer cites the PDF').toContain(PDF_NAME);
      } else {
        // The dev spend guard holds the model (owner): the person reads why, in words, and the
        // question stays to send again. The stubbed-model test proves the citation instead.
        expect(EXPECT_MODEL, `the model did not answer: ${grounded.error}`).toBe(false);
        expect(grounded.error).toMatch(/model did not answer|Model asistenta neodpovedal/);
        test.info().annotations.push({ type: 'model', description: `held: ${grounded.error}` });
      }

      // 6. Removed, the source leaves the list and the answers.
      await remove(page, FILES);
      await expect(async () => {
        await openKnowledge(page);
        await expect(sourceRow(page, FILES)).toHaveCount(0, { timeout: 5_000 });
      }).toPass({ timeout: 600_000, intervals: [15_000] });
      await shot(page, 'files-removed');
      if (grounded.answered) {
        const after = await ask(page, QUESTION, 'ask-after-removal');
        expect(after.sources, 'the answer no longer cites the removed PDF').not.toContain(PDF_NAME);
      }
    } finally {
      await remove(page, ASSISTANT, 'assistantdeployments');
      await remove(page, FILES);
      await remove(page, WEB);
      await context.close();
    }
  });
});

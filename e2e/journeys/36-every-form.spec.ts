import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Browser, Page } from '@playwright/test';
import { test, expect, requireEnv, signIn } from '../fixtures/portal.js';
import { shot, writeIndex } from '../audit/shoot.js';

/**
 * Journey 36 (T-4824): every Portal form, opened, filled, checked and broken, as the steward, with
 * a screenshot of each state at 1440 and 400 px. One fixture per form kind in `fixtures/forms/`
 * says where the form opens, the minimal valid example (demo values only), the one invalid value
 * and the variations worth their own pictures (a data source per type, an endpoint per audience).
 *
 * Read-only: after sign-in every request to the Portal's API that is not a GET or a dry run
 * (`dryRun=All`, the Check) is refused in the browser before it leaves, so Propose is never sent;
 * the one exception is the viewer's direct save, which must answer 401 or 403.
 *
 * Each form is audited on its New state against the rules of T-4824 (`AUDIT` below); every finding
 * goes to `findings.json` beside the screenshots, and `index.html` there is the gallery. The run
 * fails on a finding that `fixtures/forms/allowed.json` does not justify, on fewer than 100
 * screenshots, on a secret-shaped value on screen, and when the viewer can save.
 *
 *   PORTAL_USER=… PORTAL_PASSWORD=… PORTAL_VIEWER_USER=… PORTAL_VIEWER_PASSWORD=… \
 *     npx playwright test --config e2e/playwright.config.ts journeys/36-every-form
 *
 * Runs in the deployer's hourly live batch, never in the fast lane.
 */
const PROJECT = process.env.PORTAL_FORMS_PROJECT ?? 'helsinki';
const FIXTURES = path.join(__dirname, '..', 'fixtures', 'forms');
const DIR = process.env.SHOTS_DIR ?? path.join(process.env.JC_REPORTS_DIR ?? 'e2e/reports', 'every-form');
const WIDTHS = { desktop: { width: 1440, height: 900 }, phone: { width: 400, height: 860 } } as const;
const MIN_SHOTS = 100;

/** A control on the form, found by its label; one of value, option or checked. */
interface Step {
  label: string;
  value?: string;
  option?: string;
  checked?: boolean;
}
interface FormFixture {
  kind: string;
  family: string;
  uischema: string;
  /** Where New opens: a path, and the buttons or tabs to press there, in turn, when the form has no address. */
  open: { path: string; button?: string | string[] };
  /** The collection one existing record is read from, for its edit form. */
  plural?: string;
  fill: Step[];
  invalid?: Step;
  variations?: { name: string; steps: Step[] }[];
}
interface Finding {
  file: string;
  route: string;
  width: number;
  lang: 'en';
  who: 'steward' | 'viewer';
  rule: string;
  severity: 'blocker' | 'major' | 'minor';
  description: string;
  fix: string;
  allowed?: string;
}

const fixtures: FormFixture[] = readdirSync(FIXTURES)
  .filter((file) => file.endsWith('.json') && file !== 'allowed.json')
  .sort()
  .map((file) => JSON.parse(readFileSync(path.join(FIXTURES, file), 'utf8')) as FormFixture);
/** `<kind>:<rule>:<field>` → why the finding stays; anything else fails the run. */
const allowed = JSON.parse(readFileSync(path.join(FIXTURES, 'allowed.json'), 'utf8')) as Record<string, string>;

/** Shapes of credentials that must never be on a form: tokens, keys, JWTs. */
const SECRET_SHAPES = [
  /gh[pousr]_[A-Za-z0-9]{20,}/,
  /github_pat_[A-Za-z0-9_]{20,}/,
  /sk-(or-v1-)?[A-Za-z0-9]{24,}/,
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\./,
  /AKIA[0-9A-Z]{16}/,
  /AGE-SECRET-KEY-1[0-9A-Z]{20,}/,
];

const findings: Finding[] = [];
const refused = new Set<string>();
let shots = 0;
const sent: number[] = [];

const at = (route: string) => route.replace('{project}', PROJECT);

/** The edge allows one address 300 requests a minute: stay under 200 of ours. */
async function pace(page: Page): Promise<void> {
  for (;;) {
    const now = Date.now();
    while (sent.length && sent[0] < now - 61_000) sent.shift();
    if (sent.length < 150) return;
    await page.waitForTimeout(sent[0] + 61_000 - now);
  }
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined);
  await page.locator('h1:visible').first().waitFor({ timeout: 30_000 }).catch(() => undefined);
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 20_000 }).catch(() => undefined);
  await page.waitForTimeout(800);
}

/** Opens `route`; a page whose chunks failed to load (the edge's 429) is opened again after a minute. */
async function open(page: Page, route: string): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    await pace(page);
    await page.goto(`${route}${route.includes('?') ? '&' : '?'}lang=en`, { waitUntil: 'load' });
    await settle(page);
    // The shell's own sentence when a script chunk was refused, most often by the edge's 429.
    const broken = await page.getByText(/the portal did not load|part of the portal did not load/i).first().isVisible().catch(() => false);
    if (!broken || attempt === 3) return;
    await page.waitForTimeout(61_000);
  }
}

async function snap(page: Page, fixture: FormFixture, state: string, who: 'steward' | 'viewer' = 'steward'): Promise<void> {
  for (const [name, size] of Object.entries(WIDTHS)) {
    await page.setViewportSize(size);
    await page.waitForTimeout(250);
    await noSecretOnScreen(page, `${fixture.kind} ${state}`);
    if (await shot(page, { area: 'form', route: fixture.kind, state, who, lang: 'en' }, DIR)) shots++;
  }
  await page.setViewportSize(WIDTHS.desktop);
}

async function noSecretOnScreen(page: Page, where: string): Promise<void> {
  const text = await page.evaluate(() => {
    const values = Array.from(document.querySelectorAll('input,textarea')).map((e) => (e as HTMLInputElement).value);
    return `${document.body.innerText}\n${values.join('\n')}`;
  });
  const hit = SECRET_SHAPES.find((shape) => shape.test(text));
  expect(hit, `${where}: a value shaped like a credential is on screen`).toBeUndefined();
}

/**
 * The form's controls by their label: a native select, a combobox, a checkbox or a text field. A
 * field in a folded group is unfolded first. Returns true, or what was missing.
 */
async function apply(page: Page, step: Step): Promise<true | string> {
  const label = new RegExp(`^\\s*${step.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\*?\\s*$`, 'i');
  let control = page.getByLabel(label).locator('visible=true').first();
  if (!(await control.count())) {
    for (const summary of await page.locator('details:not([open]) > summary').locator('visible=true').all()) {
      await summary.click().catch(() => undefined);
    }
    control = page.getByLabel(label).locator('visible=true').first();
  }
  if (!(await control.count())) return step.label;
  const tag = await control.evaluate((e) => `${e.tagName}:${e.getAttribute('type') ?? ''}:${e.getAttribute('role') ?? ''}`);
  if (step.checked !== undefined) {
    await control.setChecked(step.checked);
  } else if (step.option !== undefined && tag.startsWith('SELECT')) {
    const options = await control.locator('option').allInnerTexts();
    const pick = options.find((text) => new RegExp(step.option as string, 'i').test(text));
    if (!pick) return `${step.label} = /${step.option}/ (offers ${options.join(' | ')})`;
    await control.selectOption({ label: pick });
  } else if (step.option !== undefined) {
    await control.click();
    const option = page.getByRole('option', { name: new RegExp(step.option, 'i') }).first();
    if (!(await option.isVisible({ timeout: 5_000 }).catch(() => false))) {
      const offered = await page.getByRole('option').allInnerTexts();
      await page.keyboard.press('Escape');
      return `${step.label} = /${step.option}/ (offers ${offered.join(' | ')})`;
    }
    await option.click();
  } else {
    await control.fill(step.value ?? '');
    await control.blur();
  }
  await page.waitForTimeout(300);
  return true;
}

/**
 * Presses Check and waits for its answer: the verdict chip saying something other than what it
 * said, the form's own refusal, or the server's refusal under the button. `null` when the form
 * has no Check (a dialog that opens a project, the model editor), `undefined` when none came.
 */
async function check(page: Page): Promise<string | null | undefined> {
  const button = page.getByRole('button', { name: /^check$/i }).locator('visible=true').first();
  if (!(await button.count()) || !(await button.isEnabled())) return null;
  const chip = page.getByTestId('draft-verdict').first();
  const before = (await chip.textContent().catch(() => null)) ?? '';
  await button.click();
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const stopped = await page.getByTestId('check-stopped').first().textContent().catch(() => null);
    if (stopped) return `stopped: ${stopped.trim()}`;
    const refused = await page.getByTestId('footer-error').first().textContent().catch(() => null);
    if (refused) return `refused: ${refused.trim()}`;
    const now = ((await chip.textContent().catch(() => null)) ?? '').trim();
    if (now && now !== before.trim() && !/checking/i.test(now)) return now;
    await page.waitForTimeout(500);
  }
  return undefined;
}

interface Field {
  label: string;
  id: string;
  help: string;
  required: boolean;
  marked: boolean;
  experimental: boolean;
  /** A checkbox or radio: a choice in a list, not a field of its own when the form is counted. */
  choice: boolean;
}

/** What a person sees of each visible field: its label, its help, and whether it says it is required. */
async function fields(page: Page): Promise<{ fields: Field[] }> {
  return page.evaluate(() => {
    const visible = (e: Element) => {
      const r = (e as HTMLElement).getBoundingClientRect();
      return r.width > 0 && r.height > 0 && !e.closest('details:not([open]) > :not(summary)');
    };
    // An open dialog's form, else the page's form with the most controls: "Fill this from a
    // sentence" is a form of its own above it.
    const dialog = Array.from(document.querySelectorAll('[role=dialog]')).find(visible);
    const root = dialog ?? document.querySelector('main') ?? document.body;
    const controls = (node: Element) => node.querySelectorAll('input,select,textarea').length;
    // A side panel (the app generator) sits outside main: the page itself is the root then.
    const scope = controls(root) ? root : document.body;
    const form = Array.from(scope.querySelectorAll('form')).sort((a, b) => controls(b) - controls(a))[0] ?? scope;
    const seen = new Set<string>();
    const out: Field[] = [];
    for (const e of Array.from(form.querySelectorAll('input,select,textarea,[role=combobox]'))) {
      const el = e as HTMLInputElement;
      // A table of controls (a mapping's slot derivations) is one field, its rows named by their row.
      const table = el.closest('table');
      if (table) {
        if (!table.id) table.id = `audit-table-${seen.size}`;
        if (seen.has(table.id)) continue;
      }
      if (!visible(el) || el.type === 'hidden' || el.type === 'search' || el.closest('[role=search],nav,[data-form-audit=skip]')) continue;
      // A set of checkboxes or radios under one legend is one field, with the legend's help.
      const fieldset = (el.type === 'radio' || el.type === 'checkbox') ? el.closest('fieldset') : null;
      const set = fieldset?.querySelector('legend') ? fieldset : null;
      if (set && !set.id) set.id = `audit-set-${seen.size}`;
      const group = table ? table.id : set ? set.id : (el.type === 'radio' || el.type === 'checkbox') && el.name ? el.name : el.id;
      if (group && seen.has(group)) continue;
      if (group) seen.add(group);
      const byId = (ids: string | null) =>
        (ids ?? '').split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim() ?? '').filter(Boolean).join(' ');
      const legend = el.closest('fieldset')?.querySelector('legend')?.textContent?.trim() ?? '';
      const own = Array.from(el.labels ?? []).map((l) => l.textContent?.trim() ?? '').join(' ') || el.getAttribute('aria-label') || byId(el.getAttribute('aria-labelledby'));
      const label = ((set && legend) || own || legend).replace(/\s+/g, ' ').trim();
      const help = table ? '' : byId((set ?? el).getAttribute('aria-describedby') ?? el.getAttribute('aria-describedby')).replace(/\s+/g, ' ').trim();
      const required = el.required || el.getAttribute('aria-required') === 'true';
      out.push({
        label,
        id: el.id || el.name || '',
        help,
        required,
        marked: /\*|required|povinn/i.test(label),
        experimental: /experimental|experiment[aá]ln/i.test(`${label} ${help}`),
        choice: Boolean(table) || (!set && (el.type === 'checkbox' || el.type === 'radio')),
      });
    }
    return { fields: out };
  });
}

/** The help a uischema writes for each field, in English: what a cut help is compared with. */
function schemaHelp(uischema: string): string[] {
  // The Portal's source tree, when it sits beside this repository; otherwise only the punctuation rule.
  const dir = process.env.PORTAL_FORMS_DIR ?? path.join(__dirname, '..', '..', '..', 'joinedcontext-portal', 'ui', 'src', 'schemas', 'forms');
  const file = path.join(dir, `${uischema}.uischema.yaml`);
  if (!existsSync(file)) return [];
  return [...readFileSync(file, 'utf8').matchAll(/^\s+en: (?:>-\s*\n)?([\s\S]*?)(?=\n\s+(?:sk|cs|de):)/gm)].map((m) =>
    m[1].replace(/\s+/g, ' ').replace(/^["']|["']$/g, '').trim(),
  );
}

function record(fixture: FormFixture, route: string, width: number, rule: string, field: string, description: string, fix: string, severity: Finding['severity'] = 'major'): void {
  const key = `${fixture.kind}:${rule}:${field}`;
  findings.push({
    file: `ui/src/schemas/forms/${fixture.uischema}.uischema.yaml`,
    route,
    width,
    lang: 'en',
    who: 'steward',
    rule,
    severity,
    description: `${field}: ${description}`,
    fix,
    ...(allowed[key] ? { allowed: allowed[key] } : {}),
  });
}

const RAW = /^(root_|[a-z]+[A-Z][A-Za-z]*$|[a-z]+_[a-z_]+$)/;

/** The T-4824 rules, read off the New state of one form. */
async function audit(page: Page, fixture: FormFixture, route: string): Promise<void> {
  const { fields: list } = await fields(page);
  const helps = schemaHelp(fixture.uischema);
  for (const f of list) {
    const name = f.label || f.id || '(unnamed)';
    if (!f.label) record(fixture, route, 1440, 'label', name, 'has no label a screen reader reads', 'give the field a title in the uischema or the form schema', 'blocker');
    else if (RAW.test(f.label.replace(/\*$/, '').trim())) record(fixture, route, 1440, 'raw-name', name, 'shows the schema name, not words', 'give the field a title in the uischema');
    // A choice in a list is named by its own words; the list's legend carries the help.
    if (!f.help && !f.choice) record(fixture, route, 1440, 'help', name, 'has no help text', 'write fields.<name>.help in the uischema', 'minor');
    else if (f.help) {
      const full = helps.find((h) => h.length > f.help.length && h.startsWith(f.help));
      if (full || !/[.!?:)…»"]$/.test(f.help)) record(fixture, route, 1440, 'help-cut', name, `help ends mid-sentence: "…${f.help.slice(-24)}"`, 'end the help with a full stop at its source');
    }
    // An array item (`root_roles_0`) is required inside its list; the list's heading carries the mark.
    if (f.required && !f.marked && !/_\d+$/.test(f.id)) record(fixture, route, 1440, 'required', name, 'is required but not marked', 'mark it as required');
    if (f.experimental) record(fixture, route, 1440, 'advanced', name, 'experimental field outside a folded Advanced group', 'move it into a folded group');
  }
  const counted = list.filter((f) => !f.choice).length;
  if (counted > 12) record(fixture, route, 1440, 'crowded', 'form', `${counted} fields on the first screen`, 'fold the rarely used groups');
  await page.setViewportSize(WIDTHS.phone);
  await page.waitForTimeout(300);
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (sideways > 1) record(fixture, route, 400, 'overflow', 'page', `scrolls sideways by ${sideways} px at 400 px`, 'let the widest control wrap');
  await page.setViewportSize(WIDTHS.desktop);
}

/** Opens a fixture's New form: `form`, `denied` (New is there, closed to this role) or `none`. */
async function openNew(page: Page, fixture: FormFixture): Promise<'form' | 'denied' | 'none'> {
  await open(page, at(fixture.open.path));
  for (const name of [fixture.open.button ?? []].flat()) {
    const pattern = new RegExp(name, 'i');
    const button = page.getByRole('button', { name: pattern }).or(page.getByRole('link', { name: pattern }))
      .or(page.getByRole('tab', { name: pattern })).locator('visible=true').first();
    if (!(await button.isVisible({ timeout: 10_000 }).catch(() => false))) return 'none';
    if (!(await button.isEnabled()) || (await button.getAttribute('aria-disabled')) === 'true') return 'denied';
    await button.click();
    await settle(page);
  }
  return (await fields(page)).fields.length > 0 ? 'form' : 'none';
}

async function firstName(page: Page, plural: string): Promise<string | undefined> {
  const project = plural.startsWith('org:') ? 'org' : PROJECT;
  const answer = await page.request.get(`/api/v1/projects/${project}/${plural.replace(/^org:/, '')}`);
  if (!answer.ok()) return undefined;
  const body = (await answer.json()) as { items?: { metadata?: { name?: string } }[] };
  return body.items?.find((item) => item.metadata?.name)?.metadata?.name;
}

/** One form; a step that throws is a finding of that form, and the walk goes on to the next. */
async function walk(page: Page, fixture: FormFixture): Promise<void> {
  try {
    await walkForm(page, fixture);
  } catch (error) {
    record(fixture, at(fixture.open.path), 1440, 'error', 'walk', (error as Error).message.split('\n')[0].slice(0, 200), 'see the trace of this run', 'blocker');
    await page.setViewportSize(WIDTHS.desktop);
  }
}

async function walkForm(page: Page, fixture: FormFixture): Promise<void> {
  const route = at(fixture.open.path);
  await test.step(`${fixture.kind}: New`, async () => {
    const opened = await openNew(page, fixture);
    if (opened !== 'form') {
      if (opened === 'denied') record(fixture, route, 1440, 'permission', 'New', 'New is closed to the steward; it says why', 'none: an organization administrator writes this kind', 'minor');
      else record(fixture, route, 1440, 'open', 'form', 'New does not open a form for the steward', 'offer New where the kind is listed', 'blocker');
      await snap(page, fixture, opened);
      return;
    }
    await snap(page, fixture, 'new');
    await audit(page, fixture, route);
    const missing: string[] = [];
    for (const step of fixture.fill) {
      const done = await apply(page, step);
      if (done !== true) missing.push(done);
    }
    if (missing.length) record(fixture, route, 1440, 'fixture', missing.join(', '), 'the fixture names a field the form does not show', 'update the fixture or the form', 'minor');
    await snap(page, fixture, 'filled');
    const verdict = await check(page);
    if (verdict === undefined) record(fixture, route, 1440, 'check', 'Check', 'Check never reached a verdict in 30 s', 'make Check answer or say why it cannot', 'blocker');
    await snap(page, fixture, 'checked');
    if (fixture.invalid) {
      await apply(page, fixture.invalid);
      const after = await check(page);
      const flagged = await page.locator('[aria-invalid="true"]:visible, [role=alert]:visible').count();
      if (/passed|ready|green/i.test(after ?? '') && flagged === 0) {
        record(fixture, route, 1440, 'invalid', fixture.invalid.label, `accepted the invalid value "${fixture.invalid.value ?? fixture.invalid.option}"`, 'validate the field in the schema', 'blocker');
      }
      await snap(page, fixture, 'invalid');
    }
  });
  for (const variation of fixture.variations ?? []) {
    await test.step(`${fixture.kind}: ${variation.name}`, async () => {
      if ((await openNew(page, fixture)) !== 'form') return;
      const missing: string[] = [];
      for (const step of [...fixture.fill, ...variation.steps]) {
        const done = await apply(page, step);
        if (done !== true) missing.push(done);
      }
      if (missing.length) record(fixture, route, 1440, 'fixture', `${variation.name}: ${missing.join(', ')}`, 'the variation names a field the form does not show', 'update the fixture or the form', 'minor');
      await snap(page, fixture, `variation-${variation.name}`);
    });
  }
  if (fixture.plural) {
    await test.step(`${fixture.kind}: edit an existing one`, async () => {
      const name = await firstName(page, fixture.plural as string);
      if (!name) return;
      const base = fixture.plural?.startsWith('org:') ? '/organization' : `/projects/${PROJECT}`;
      await open(page, `${base}/${(fixture.plural as string).replace(/^org:/, '')}/${encodeURIComponent(name)}/edit`);
      await snap(page, fixture, 'edit');
    });
  }
}

/** Refuses every write but a dry run, so nothing here proposes, saves a draft or deletes. */
async function readOnly(page: Page): Promise<void> {
  page.on('request', (request) => {
    // The edge counts every request of this address, the scripts and pictures as well.
    sent.push(Date.now());
  });
  await page.route('**/api/**', (route) => {
    const request = route.request();
    if (request.method() === 'GET' || /[?&]dryRun=All\b/.test(request.url())) return route.continue();
    refused.add(`${request.method()} ${new URL(request.url()).pathname}`);
    return route.abort('blockedbyclient');
  });
}

async function viewer(browser: Browser, portal: string): Promise<void> {
  const name = process.env.PORTAL_VIEWER_USER;
  const secret = process.env.PORTAL_VIEWER_PASSWORD;
  if (!name || !secret) throw new Error('PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD are not set: the viewer half of journey 36 needs them');
  const context = await browser.newContext({ baseURL: portal, viewport: WIDTHS.desktop, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  try {
    await page.goto(portal);
    await signIn(page, name, secret, portal);
    for (const kind of ['scopedefinition', 'contextspace', 'endpoint', 'datasource']) {
      const fixture = fixtures.find((f) => f.kind === kind);
      if (!fixture) continue;
      await open(page, at(fixture.open.path));
      const propose = page.getByRole('button', { name: /propose|save|edit/i }).locator('visible=true');
      const enabled = await propose.evaluateAll((list) => list.filter((b) => !(b as HTMLButtonElement).disabled && b.getAttribute('aria-disabled') !== 'true').length);
      expect.soft(enabled, `${kind}: the viewer must see no Propose or Edit it can press`).toBe(0);
      if (await shot(page, { area: 'form', route: kind, state: 'viewer', who: 'viewer', lang: 'en' }, DIR)) shots++;
    }
    // The save a form would send, sent straight to the API: the server's rule, not the hidden button.
    const save = await page.request.post(`/api/v1/projects/${PROJECT}/scopedefinitions`, {
      data: {
        apiVersion: 'joinedcontext.com/v1alpha1',
        kind: 'ScopeDefinition',
        metadata: { name: 't4824-viewer-probe', namespace: PROJECT },
        spec: { scope: '/t4824/viewer-probe' },
      },
    });
    expect([401, 403], `a viewer's direct save answered ${save.status()}`).toContain(save.status());
  } finally {
    await context.close();
  }
}

/** The 29 kinds with a shipped form arrangement (ui/src/schemas/forms/*.uischema.yaml). */
const KINDS = [
  'agentprofile', 'app', 'assistantdeployment', 'blueprint', 'contextsourceregistration', 'contextspace', 'dashboard',
  'dataagreement', 'datamodel', 'dataoffer', 'datasource', 'dataspaceparticipant', 'endpoint', 'environment', 'group',
  'knowledgesource', 'layer', 'mapping', 'modelprojection', 'mqttbroker', 'organization', 'pipeline', 'policy',
  'project', 'role', 'scopedefinition', 'serviceaccount', 'subscription', 'syncsource',
];

test('journey 36 has a fixture for every form kind, and each allowance names one of them', () => {
  expect([...new Set(fixtures.map((f) => f.uischema))].sort()).toEqual([...KINDS].sort());
  const kinds = new Set(fixtures.map((f) => f.kind));
  expect(Object.keys(allowed).filter((key) => !kinds.has(key.split(':')[0]))).toEqual([]);
  for (const fixture of fixtures) {
    const values = JSON.stringify(fixture);
    expect(SECRET_SHAPES.find((shape) => shape.test(values)), `${fixture.kind} holds a credential`).toBeUndefined();
  }
});

test.describe('Journey 36: every form, filled, checked and broken (T-4824)', () => {
  test.setTimeout(90 * 60_000);
  test.use({ viewport: WIDTHS.desktop });

  test('the steward opens every form; the viewer cannot save', async ({ page, browser, baseURL }) => {
    const portal = process.env.PORTAL_URL ?? baseURL ?? 'https://portal.dev.joinedcontext.com';
    mkdirSync(DIR, { recursive: true });
    await page.goto(portal);
    await signIn(page, requireEnv('PORTAL_USER'), requireEnv('PORTAL_PASSWORD'), portal);
    await readOnly(page);
    for (const fixture of fixtures) await walk(page, fixture);
    await viewer(browser, portal);

    writeFileSync(path.join(DIR, 'findings.json'), `${JSON.stringify(findings, null, 2)}\n`);
    const counted = writeIndex(DIR);
    console.log(`journey 36: ${counted} screenshots, ${findings.length} findings, refused writes: ${[...refused].join(', ') || 'none'}`);
    const open = findings.filter((f) => !f.allowed).map((f) => `${f.route} [${f.rule}] ${f.description}`);
    expect.soft(open, 'findings no allowed.json entry justifies').toEqual([]);
    expect(counted, 'the gallery must hold at least 100 screenshots').toBeGreaterThanOrEqual(MIN_SHOTS);
  });
});

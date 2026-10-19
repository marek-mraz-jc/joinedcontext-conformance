// Every eval question asked through the real widget page (owner rule 2026-10-19 05:19Z, T-4680): the
// assistant's full page /d/{publicId} in Chromium, the question typed and sent, and once the answer has
// settled (the send button back, charts and maps loaded) the whole conversation is pictured at 1280 px and
// at 400 px. The chat's event stream is kept beside the pictures, so `eval_opendata.py --events DIR`
// grades the very answer that was pictured instead of asking again.
//   node tests/assistant/screens.mjs --base https://assistant.dev.joinedcontext.com --deployment data-eval-gemini \
//     --out tasks/evidence/T-4680/screens/<run-ts> [--only od-57 od-58]
// The page is the anonymous visitor's: no token, no cookie and no personal data reach a picture.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const WIDTHS = { desktop: [1280, 900], phone: [400, 860] };
// An answer settles within the service's own limit (45 s for a data question) plus the stream's end.
const SETTLE_MS = 120_000;

function args(argv) {
  const out = { only: [] };
  for (let i = 2; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, "");
    if (key === "only") {
      while (argv[i + 1] && !argv[i + 1].startsWith("--")) out.only.push(argv[++i]);
    } else {
      out[key] = argv[++i];
    }
  }
  for (const need of ["base", "deployment", "out"]) {
    if (!out[need]) throw new Error(`--${need} is required`);
  }
  if (!/eval/.test(out.deployment)) throw new Error(`only an eval copy is asked, not ${out.deployment}`);
  return out;
}

// Chromium hands Playwright an event stream decoded as Windows-1252 (no charset on
// text/event-stream), and the bytes come back UTF-8 encoded a second time: "Niektoré" was saved as
// "NiektorÃ©" (T-4680 before pass). Each character maps back to its Windows-1252 byte; the five
// bytes that code page leaves undefined decode to their own code point, so they map back too.
const CP1252 = new Map(
  [...new TextDecoder("windows-1252").decode(Uint8Array.from({ length: 256 }, (_, i) => i))].map((c, i) => [c, i]),
);

/** The text as it was sent: repaired when it round-trips through Windows-1252 to valid UTF-8. */
export function undouble(text) {
  const bytes = [];
  for (const c of text) {
    const byte = CP1252.get(c);
    if (byte === undefined) return text;
    bytes.push(byte);
  }
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(bytes));
  } catch {
    return text;
  }
}

function selftest() {
  const sent = "Niektoré dátové zdroje – „Žilina“ 11,506 µg/m³ €";
  const doubled = new TextDecoder("windows-1252").decode(new TextEncoder().encode(sent));
  assert.notEqual(doubled, sent);
  assert.equal(undouble(doubled), sent);
  // Text that arrived right is left alone, with or without characters outside Windows-1252.
  assert.equal(undouble(sent), sent);
  assert.equal(undouble("Banská Bystrica é"), "Banská Bystrica é");
  assert.equal(undouble("event: done\ndata: {}\n\n"), "event: done\ndata: {}\n\n");
  assert.equal(undouble(""), "");
  console.error("screens.mjs selftest ok");
}

/** Grows the viewport to the conversation's whole height, so one picture holds every turn. */
async function wholeHeight(page, width) {
  const height = await page.evaluate(() => {
    const log = document.querySelector(".jc-log");
    const rest = document.documentElement.clientHeight - (log ? log.clientHeight : 0);
    return Math.ceil(rest + (log ? log.scrollHeight : 0));
  });
  await page.setViewportSize({ width, height: Math.max(height, WIDTHS.desktop[1]) });
  await page.waitForTimeout(500);
}

// The visitor's browser speaks the question's language, as a resident's would.
const LOCALES = { sk: "sk-SK", cs: "cs-CZ", en: "en-GB" };

/** Sends one message and waits until its answer has settled; the ms it took. */
async function send(page, text) {
  const started = Date.now();
  const input = page.locator(".jc-chat textarea").last();
  await input.fill(text);
  await input.press("Enter");
  // The send button hides while an answer streams and comes back when it has settled.
  const button = page.locator('.jc-chat button[type="submit"]').last();
  await button.waitFor({ state: "hidden", timeout: 10_000 }).catch(() => {});
  await button.waitFor({ state: "visible", timeout: SETTLE_MS });
  return Date.now() - started;
}

async function ask(browser, base, deployment, q) {
  const context = await browser.newContext({
    viewport: { width: WIDTHS.desktop[0], height: WIDTHS.desktop[1] },
    locale: LOCALES[q.lang] || "sk-SK",
  });
  const page = await context.newPage();
  // The body of the last chat POST, read once the stream has ended.
  let stream = Promise.resolve("");
  page.on("response", (response) => {
    if (response.request().method() === "POST" && response.url().endsWith(`/api/v1/d/${deployment}/chat`)) {
      stream = response.body().then((bytes) => undouble(bytes.toString("utf8"))).catch(() => "");
    }
  });
  await page.goto(`${base.replace(/\/$/, "")}/d/${deployment}`, { waitUntil: "networkidle" });
  // A follow-up is asked after its earlier questions, in the same conversation; the stream kept is
  // the last one, the follow-up's own.
  for (const turn of q.history || []) {
    if (turn.role === "user") await send(page, turn.text);
  }
  const settledMs = await send(page, q.q);
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  return { context, page, stream: await stream, settledMs };
}

async function main() {
  const opts = args(process.argv);
  const spec = JSON.parse(readFileSync(join(here, "opendata_questions.json"), "utf8"));
  const questions = spec.questions.filter((q) => !opts.only.length || opts.only.includes(q.id));
  mkdirSync(opts.out, { recursive: true });
  const browser = await chromium.launch();
  const timings = {};
  try {
    for (const q of questions) {
      const runs = q.repeat || 1;
      for (let run = 1; run <= runs; run++) {
        const name = runs > 1 ? `${q.id}-${run}` : q.id;
        let asked;
        try {
          asked = await ask(browser, opts.base, opts.deployment, q);
        } catch (err) {
          timings[name] = { error: String(err.message || err) };
          console.error(`${name}: ${timings[name].error}`);
          continue;
        }
        const { context, page, stream, settledMs } = asked;
        writeFileSync(join(opts.out, `${name}.sse`), stream);
        for (const [label, [width]] of Object.entries(WIDTHS)) {
          await page.setViewportSize({ width, height: WIDTHS[label][1] });
          await wholeHeight(page, width);
          await page.screenshot({ path: join(opts.out, `${name}-${label}.png`), fullPage: true });
        }
        timings[name] = { settledMs };
        console.error(`${name}: settled in ${(settledMs / 1000).toFixed(1)} s`);
        await context.close();
        await new Promise((r) => setTimeout(r, Number(opts.pace || 7) * 1000));
      }
    }
  } finally {
    writeFileSync(join(opts.out, "timings.json"), JSON.stringify(timings, null, 1));
    await browser.close();
  }
}

if (process.argv.includes("--selftest")) {
  selftest();
} else {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}

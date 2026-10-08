import fs from 'node:fs';
import path from 'node:path';
import type { Page, TestInfo } from '@playwright/test';

/**
 * What a usability journey measures of one task (T-3282): the clicks a person made, the documents
 * the browser loaded, the views the address moved through and the seconds it took, with the time
 * of every step, so the slowest step names the friction to file.
 */
export interface Measure {
  task: string;
  clicks: number;
  pageLoads: number;
  views: number;
  seconds: number;
  steps: { step: string; seconds: number }[];
  slowest: { step: string; seconds: number } | null;
}

/** The ceiling every task is held to: the 60-seconds roadmap's minute, and ten clicks. */
export const BUDGET = { seconds: 60, clicks: 10 };

/**
 * Starts counting on `page`. Clicks are counted in the page, in the capture phase, from trusted
 * events only, and reported through a binding so a full page load does not lose the count.
 */
export async function startMeter(page: Page, task: string) {
  let clicks = 0;
  let pageLoads = 0;
  const views = new Set<string>();
  await page.exposeBinding('__jcUsabilityClick', () => {
    clicks += 1;
  });
  const listen = () => {
    document.addEventListener(
      'click',
      (event) => {
        if (event.isTrusted) (window as unknown as { __jcUsabilityClick: () => void }).__jcUsabilityClick();
      },
      true,
    );
  };
  // Every document loaded from now on, and the one already open.
  await page.addInitScript(listen);
  await page.evaluate(listen);
  page.on('load', () => {
    pageLoads += 1;
  });
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) views.add(new URL(frame.url()).pathname);
  });
  const started = Date.now();
  let last = started;
  const steps: Measure['steps'] = [];
  return {
    /** Marks a step as reached: the time since the step before is what it cost. */
    step(name: string): void {
      const now = Date.now();
      steps.push({ step: name, seconds: Math.round((now - last) / 100) / 10 });
      last = now;
    },
    done(): Measure {
      const slowest = steps.reduce<Measure['slowest']>((worst, step) => (!worst || step.seconds > worst.seconds ? step : worst), null);
      return {
        task,
        clicks,
        pageLoads,
        views: views.size,
        seconds: Math.round((Date.now() - started) / 100) / 10,
        steps,
        slowest,
      };
    },
  };
}

/** Writes the measure where the run's reports go and attaches it to the test. */
export async function report(measure: Measure, info: TestInfo): Promise<void> {
  const dir = path.join(process.env.JC_REPORTS_DIR || 'e2e/reports', 'usability');
  fs.mkdirSync(dir, { recursive: true });
  const body = JSON.stringify(measure, null, 2);
  fs.writeFileSync(path.join(dir, `${measure.task}.json`), body);
  await info.attach(`${measure.task}.json`, { body, contentType: 'application/json' });
}

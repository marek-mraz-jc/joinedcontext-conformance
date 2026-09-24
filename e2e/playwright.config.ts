import { defineConfig, devices } from '@playwright/test';
import type { ReporterDescription } from '@playwright/test';
import path from 'node:path';

const baseURL = process.env.BASE_URL || 'https://dev.joinedcontext.com';
// /tests and /e2e are read-only in the runner image; reports always go to a writable directory.
const reportDir = process.env.JC_REPORTS_DIR || 'e2e/reports';

/**
 * One annotation per failing test on CI, so a red run names the journey and the step in the run
 * summary instead of only inside the downloaded html report (T-0362).
 */
function reporters(): ReporterDescription[] {
  const list: ReporterDescription[] = [['list']];
  if (process.env.CI) {
    list.push(['github']);
  }
  list.push(['junit', { outputFile: path.join(reportDir, 'results.xml') }]);
  list.push(['html', { outputFolder: path.join(reportDir, 'html'), open: 'never' }]);
  return list;
}

export default defineConfig({
  testDir: '.',
  outputDir: path.join(reportDir, 'artifacts'),
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: reporters(),
  use: {
    baseURL,
    headless: true,
    locale: 'en-GB',
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    ...(process.env.E2E_ALL_BROWSERS
      ? [
          {
            name: 'firefox',
            use: { ...devices['Desktop Firefox'] },
          },
          {
            name: 'webkit',
            use: { ...devices['Desktop Safari'] },
          },
        ]
      : []),
  ],
});

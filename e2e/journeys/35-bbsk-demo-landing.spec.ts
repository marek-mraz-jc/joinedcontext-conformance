import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

/**
 * T-4740: the BBSK demo's landing page as a visitor meets it, at `JC_BBSK_DEMO_URL` (prod
 * https://bbsk.smartcity.joinedcontext.com, the dev preview https://bbsk-demo.apps.dev.joinedcontext.com).
 * The DEMO ribbon on every width, bbsk.sk's own sections, the six question cards, the assistant
 * docked beside the page on a wide screen and full-screen on a phone, and no request to any host but the page's own and the
 * assistant's. It asks the assistant nothing, so it spends no model token. With `JC_EVIDENCE_DIR`
 * set it keeps one screenshot per width there.
 */
const SITE = (process.env.JC_BBSK_DEMO_URL || 'https://bbsk-demo.apps.dev.joinedcontext.com').replace(/\/$/, '');
const DOMAIN = new URL(SITE).hostname.replace(/^bbsk\.|^bbsk-demo\.apps\./, '');
const ASSISTANT = `assistant.${DOMAIN}`;
const EVIDENCE = process.env.JC_EVIDENCE_DIR;
// One page, the snapshot of the www.bbsk.sk home page in Slovak (owner 2026-10-19T12:55Z).
const RIBBON = {
  '/': 'DEMO · ukážka od joinedcontext, nie oficiálna stránka BBSK',
};

for (const width of [1440, 390]) {
  for (const [path, ribbon] of Object.entries(RIBBON)) {
    test(`BBSK demo ${path} at ${width} px: DEMO, six cards, the docked assistant, no third party`, async ({ page }) => {
      await page.setViewportSize({ width, height: width > 600 ? 900 : 844 });
      const hosts = new Set<string>();
      page.on('request', (request) => hosts.add(new URL(request.url()).host));
      const answer = await page.goto(SITE + path);
      expect(answer?.status()).toBe(200);
      expect(answer?.headers()['x-robots-tag']).toContain('noindex');

      await expect(page.locator('.ribbon')).toHaveText(ribbon);
      await expect(page.locator('.ribbon')).toBeVisible();
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');
      await expect(page.locator('.cards button')).toHaveCount(6);
      await expect(page.locator('#sluzby')).toHaveText('Služby');
      await expect(page.locator('img')).toHaveCount(0);
      if (EVIDENCE) {
        mkdirSync(EVIDENCE, { recursive: true });
        await page.screenshot({ path: join(EVIDENCE, `landing-sk-${width}.png`), fullPage: true });
      }

      await page.locator('.cards button').first().click();
      const frame = page.locator('#jc-assistant iframe');
      await expect(frame).toBeVisible();
      // The assistant's own page loads inside: its frame-ancestors admit this host.
      await expect(page.frameLocator('#jc-assistant iframe').locator('body')).not.toBeEmpty({ timeout: 20_000 });
      const docked = await page.evaluate(() => document.documentElement.classList.contains('jc-docked'));
      expect(docked).toBe(width >= 1100);
      if (width >= 1100) {
        // Docked, the page shrinks beside the panel instead of lying under it.
        const box = await frame.boundingBox();
        const main = await page.locator('main').boundingBox();
        expect(box && main && main.x + main.width <= box.x + 1).toBeTruthy();
      }
      if (EVIDENCE) {
        await page.screenshot({ path: join(EVIDENCE, `landing-sk-${width}-open.png`) });
      }
      const others = [...hosts].filter((host) => host !== new URL(SITE).host && host !== ASSISTANT);
      expect(others, 'hosts beyond the page and its assistant').toEqual([]);
    });
  }
}

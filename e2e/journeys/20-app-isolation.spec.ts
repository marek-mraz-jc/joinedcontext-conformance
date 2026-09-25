import type { BrowserContext, Page } from '@playwright/test';
import { test, expect } from '../fixtures/portal.js';

/**
 * Journey 20 (T-2839): two published Apps cannot reach each other, the Portal or the forge
 * (ADR-N-037, AP-133, AP-135). Each App is served on `{name}.apps.{apex}`, a browser origin of
 * its own, so A's page reads none of B's storage, A's session never rides to B's host, and the
 * platform's paths are not routed on an App's host. The old `/apps/{name}/` path on the apex
 * only redirects, and sets no cookie while it does.
 *
 * The pod half of AP-135 (A's pod cannot connect outside its policy) is
 * `tests/security/test_app_egress.py`, replayed against a dump of dev's App NetworkPolicies.
 */
const appA = process.env.PORTAL_APP_A;
const appB = process.env.PORTAL_APP_B;
const slugB = process.env.PORTAL_APP_B_SLUG;

/** The apex every App's host sits under: `PORTAL_APPS_DOMAIN`, else the Portal's host without `portal.`. */
function apex(baseURL: string | undefined): string {
  const configured = process.env.PORTAL_APPS_DOMAIN;
  if (configured) {
    return configured;
  }
  const host = new URL(baseURL ?? 'https://dev.joinedcontext.com').hostname;
  return host.replace(/^portal\./, '');
}

function originOf(name: string, baseURL: string | undefined): string {
  return `https://${name}.apps.${apex(baseURL)}`;
}

/** Opens an App's host in the signed-in context and waits until the App, not the realm, is shown. */
async function openApp(context: BrowserContext, origin: string): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${origin}/`);
  await page.waitForURL((url) => url.origin === origin && !url.pathname.startsWith('/callback'), {
    timeout: 45_000,
  });
  return page;
}

test.describe('Journey 20: every App on its own origin (T-2839, AP-135)', () => {
  test.skip(
    !appA || !appB || !slugB,
    'PORTAL_APP_A, PORTAL_APP_B and PORTAL_APP_B_SLUG name two published Apps and B\'s endpoint; see e2e/README.md',
  );

  test('AP-135 A\'s page reads none of B\'s localStorage', async ({ steward, baseURL }) => {
    const pageB = await openApp(steward.context, originOf(appB!, baseURL));
    const probe = `ap135-${Date.now()}`;
    await pageB.evaluate((value) => window.localStorage.setItem('jc-ap135-probe', value), probe);

    const pageA = await openApp(steward.context, originOf(appA!, baseURL));
    const seen = await pageA.evaluate(() => window.localStorage.getItem('jc-ap135-probe'));
    expect(seen, 'AP-135: A shares a storage origin with B').toBeNull();
  });

  test('AP-135 the forge, the Portal API and B\'s endpoint answer 404 on A\'s host', async ({ steward, baseURL }) => {
    const originA = originOf(appA!, baseURL);
    const pageA = await openApp(steward.context, originA);
    for (const path of ['/git/', '/api/v1/projects', `/api/endpoint/${slugB}/ngsi-ld/v1/entities`]) {
      const response = await pageA.request.get(`${originA}${path}`, { maxRedirects: 0 });
      expect(response.status(), `AP-135: ${path} on A's host, with A's session`).toBe(404);
    }
  });

  test('AP-133 A\'s session cookie stays on A\'s host and never rides to B\'s', async ({ steward, baseURL }) => {
    const originA = originOf(appA!, baseURL);
    const originB = originOf(appB!, baseURL);
    await openApp(steward.context, originA);

    const cookiesA = await steward.context.cookies(originA);
    expect(
      cookiesA.length,
      'PORTAL_APP_A must ask for a login, so it has a session that could leak',
    ).toBeGreaterThan(0);
    for (const cookie of cookiesA) {
      expect(cookie.domain, `AP-133: ${cookie.name} is scoped wider than A's host`).toBe(new URL(originA).hostname);
    }

    const sent: Promise<string | null>[] = [];
    const pageB = await steward.context.newPage();
    pageB.on('request', (request) => {
      if (new URL(request.url()).origin === originB) {
        sent.push(request.headerValue('cookie'));
      }
    });
    await pageB.goto(`${originB}/`);
    await pageB.waitForURL((url) => url.origin === originB, { timeout: 45_000 });
    const headers = (await Promise.all(sent)).map((value) => value ?? '');
    expect(headers.length, 'the browser sent nothing to B\'s host').toBeGreaterThan(0);
    for (const cookie of cookiesA) {
      for (const header of headers) {
        expect(header, `AP-133: A's ${cookie.name} was sent to B's host`).not.toContain(`${cookie.name}=${cookie.value}`);
      }
    }
  });

  test('AP-133 /apps/A/ on the apex answers 308 to A\'s host and sets no cookie', async ({ playwright, baseURL }) => {
    const request = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    try {
      const response = await request.get(`https://${apex(baseURL)}/apps/${appA}/`, { maxRedirects: 0 });
      expect(response.status(), 'AP-133: the old path only redirects').toBe(308);
      expect(response.headers().location).toBe(`${originOf(appA!, baseURL)}/`);
      const cookies = response.headersArray().filter((header) => header.name.toLowerCase() === 'set-cookie');
      expect(cookies, 'AP-133: the redirect sets no session on the apex').toEqual([]);
    } finally {
      await request.dispose();
    }
  });
});

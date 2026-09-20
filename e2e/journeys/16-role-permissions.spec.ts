import type { Locator, Page } from '@playwright/test';
import { test, expect, openView, signIn } from '../fixtures/portal.js';

/**
 * Journey 16 (T-0604): granular roles in the Portal UI. The read-only demo viewer sees the
 * primary actions disabled with the reason (UI-44) and the API refuses the same request
 * directly (PF-50); the steward sees them enabled and opens their dialogs. TS-12: the views are
 * opened by their real navigation, the controls are the real ones, and the accessibility
 * attributes are what is asserted.
 *
 * Nothing here widens a grant: the only write attempted is one the role denies, and a 2xx there
 * is the failure.
 */
const endpoints = /^endpoints$|^koncové body$|^endpunkte$/i;
const pipelines = /^pipelines$|^kanály$|^toky$/i;
const newEndpoint = /new endpoint|nové rozhranie|neuer endpunkt/i;
const newPipeline = /new pipeline|nová pipelina|neue pipeline/i;

/** The wrapper the guard renders around a denied control: focusable, with the reason as its tooltip. */
function wrapperOf(button: Locator): Locator {
  return button.locator('xpath=..');
}

async function expectDisabledWithReason(page: Page, button: Locator, kind: string): Promise<void> {
  await expect(button, `UI-44: the '${kind}' control stays visible`).toBeVisible({ timeout: 20_000 });
  await expect(button, 'UI-44: disabled, never hidden').toBeDisabled();
  await expect(button).toHaveAttribute('aria-disabled', 'true');

  // By keyboard: the description the wrapper points at.
  const describedBy = await button.getAttribute('aria-describedby');
  expect(describedBy, 'UI-44: the reason is reachable through aria-describedby').toBeTruthy();
  const reason = new RegExp(`Disabled: your role does not permit 'propose' on '${kind}'`);
  await expect(page.locator(`[id="${describedBy as string}"]`)).toHaveText(reason);

  // By pointer: the wrapper's tooltip.
  const wrapper = wrapperOf(button);
  await expect(wrapper).toHaveAttribute('title', reason);
  await wrapper.hover();
  await wrapper.focus();
  await expect(wrapper).toBeFocused();
}

test.describe('Journey 16: granular roles, disabled with a reason (T-0604)', () => {
  test('UI-44 the viewer sees New endpoint and New pipeline disabled with the reason, and PF-50 the API says 403', async ({
    browser,
    baseURL,
  }) => {
    const user = process.env.PORTAL_VIEWER_USER;
    const pass = process.env.PORTAL_VIEWER_PASSWORD;
    test.skip(!user || !pass, 'PORTAL_VIEWER_USER and PORTAL_VIEWER_PASSWORD name the read-only demo user');

    const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true, locale: 'en-GB' });
    const page = await context.newPage();
    try {
      await page.goto('/');
      await signIn(page, user as string, pass as string);

      await openView(page, endpoints);
      await expectDisabledWithReason(page, page.getByRole('button', { name: newEndpoint }).first(), 'Endpoint');
      const project = new URL(page.url()).pathname.split('/')[2];
      expect(project, 'the endpoints view lives under a project').toBeTruthy();

      await openView(page, pipelines);
      await expectDisabledWithReason(page, page.getByRole('button', { name: newPipeline }).first(), 'Pipeline');

      // The disabled state is a guide; the Portal is the point of enforcement (PF-50). The same
      // request sent directly, with the session and the CSRF pair, is refused.
      const csrf = (await context.cookies()).find((cookie) => cookie.name === 'jc_csrf');
      expect(csrf, 'the session carries a CSRF cookie').toBeTruthy();
      const direct = await context.request.post(`/api/v1/projects/${project}/endpoints`, {
        headers: { 'x-csrf-token': csrf?.value ?? '', 'content-type': 'application/json' },
        data: {
          apiVersion: 'joinedcontext.com/v1alpha1',
          kind: 'Endpoint',
          metadata: { name: 'journey-16-must-not-land', namespace: project },
          spec: { contextSpaceRef: project, slug: 'a2b3c4d5e6f7g2h3j4k5m6n7p2', audience: 'project-list', allowedProjects: [] },
        },
      });
      expect(direct.status(), 'PF-50: a viewer proposing an Endpoint directly is refused').toBe(403);
    } finally {
      await context.close();
    }
  });

  test('the steward sees both controls enabled and each opens its dialog', async ({ steward }) => {
    const { page } = steward;

    await openView(page, endpoints);
    const endpointButton = page.getByRole('button', { name: newEndpoint }).first();
    await expect(endpointButton).toBeVisible({ timeout: 20_000 });
    await expect(endpointButton).toBeEnabled();
    expect(await endpointButton.getAttribute('aria-disabled')).not.toBe('true');
    await endpointButton.click();
    await expect(
      page.getByRole('dialog').or(page.getByRole('form')).first(),
      'New endpoint opens the endpoint form',
    ).toBeVisible({ timeout: 15_000 });
    await page.keyboard.press('Escape');

    await openView(page, pipelines);
    const pipelineButton = page.getByRole('button', { name: newPipeline }).first();
    await expect(pipelineButton).toBeVisible({ timeout: 20_000 });
    await expect(pipelineButton).toBeEnabled();
    expect(await pipelineButton.getAttribute('aria-disabled')).not.toBe('true');
    await pipelineButton.click();
    await expect(page.getByRole('dialog').first(), 'New pipeline opens the pipeline dialog').toBeVisible({
      timeout: 15_000,
    });
    await page.keyboard.press('Escape');
  });
});

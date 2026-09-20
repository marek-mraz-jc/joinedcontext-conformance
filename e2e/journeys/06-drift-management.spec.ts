import { test, expect, openView } from '../fixtures/portal.js';

/**
 * Journey 6 (T-0074): drift surfacing, lifecycle status chips, explicit Revert / Adopt actions,
 * change proposal generation from live drift, and negative validation for converged flows —
 * TS-12, UI-25, UI-26, CC-21, CC-33, CC-38.
 */
test.describe('Journey 6: Drift surfacing, revert and adopt (T-0074)', () => {
  const driftedFlow = process.env.PORTAL_DRIFTED_FLOW;
  const liveFlow = process.env.PORTAL_LIVE_FLOW;
  const galleryNav = /flow gallery|blueprint gallery|galéria|flows/i;
  const approvalsNav = /approvals|pending approvals|schválenia/i;

  test('UI-25 the drifted flow displays the Drifted lifecycle chip', async ({ steward }) => {
    test.skip(!driftedFlow, 'PORTAL_DRIFTED_FLOW is unset — drift is produced out of band, not from the browser');
    const { page } = steward;
    await openView(page, galleryNav);

    const flowItem = page.getByRole('article').filter({ hasText: driftedFlow! })
      .or(page.getByRole('row', { name: new RegExp(driftedFlow!, 'i') }))
      .or(page.getByText(driftedFlow!).locator('..'))
      .first();
    await expect(flowItem, `UI-25: flow gallery must list drifted flow "${driftedFlow}"`).toBeVisible({ timeout: 20_000 });

    // CC-33 normative lifecycle statuses: draft, pending approval, deploying, live, error, drifted
    const allowedStatuses = /draft|pending approval|deploying|live|error|drifted|čaká na schválenie|nasadzuje|nasadené|chyba|odchýlené/i;
    const chip = flowItem.locator('[data-status], [data-testid="lifecycle-status"]')
      .or(flowItem.getByText(allowedStatuses))
      .first();
    await expect(chip, 'UI-25: flow item must display a standardized lifecycle status chip').toBeVisible({ timeout: 15_000 });

    const statusText = (await chip.textContent()) || '';
    expect(
      statusText.trim(),
      `UI-25: status for drifted flow "${driftedFlow}" must indicate drifted, got "${statusText}"`,
    ).toMatch(/drifted|odchýlené|drift/i);
  });

  test('UI-26 opening drift resolution presents exactly two actions: Revert and Adopt', async ({ steward }) => {
    test.skip(!driftedFlow, 'PORTAL_DRIFTED_FLOW is unset — drift is produced out of band, not from the browser');
    const { page } = steward;
    await openView(page, galleryNav);

    const flowItem = page.getByRole('article').filter({ hasText: driftedFlow! })
      .or(page.getByRole('row', { name: new RegExp(driftedFlow!, 'i') }))
      .or(page.getByText(driftedFlow!).locator('..'))
      .first();
    await expect(flowItem, `UI-25: flow "${driftedFlow}" must be visible`).toBeVisible({ timeout: 20_000 });

    // Click the drift indicator/chip to open resolution choices
    const driftTrigger = flowItem.getByRole('button', { name: /drifted|odchýlené|drift|resolve/i })
      .or(flowItem.locator('[data-status="drifted"]'))
      .or(flowItem.getByText(/drifted|odchýlené|drift/i))
      .first();
    await driftTrigger.click();

    const dialog = page.getByRole('dialog')
      .or(page.locator('[data-testid="drift-actions"]'))
      .or(page.locator('.drift-resolution-actions'))
      .first();
    await expect(dialog, 'UI-26: clicking the drifted chip must open resolution actions dialog').toBeVisible({ timeout: 15_000 });

    // UI-26 / CC-38: exactly two resolution actions (excluding dismissal/cancel buttons)
    const actionButtons = dialog.getByRole('button').filter({
      hasNotText: /close|cancel|zavrieť|zrušiť|×/i,
    });
    const count = await actionButtons.count();
    expect(count, `UI-26: drift resolution must present exactly two actions, got ${count}`).toBe(2);

    const buttonLabels: string[] = [];
    for (let i = 0; i < count; i++) {
      buttonLabels.push((await actionButtons.nth(i).textContent()) || '');
    }

    const hasRevert = buttonLabels.some((label) => /revert|vrátiť/i.test(label));
    const hasAdopt = buttonLabels.some((label) => /adopt|prevziať/i.test(label));

    expect(hasRevert, `UI-26: drift actions must include "Revert", found: [${buttonLabels.join(', ')}]`).toBe(true);
    expect(hasAdopt, `UI-26: drift actions must include "Adopt", found: [${buttonLabels.join(', ')}]`).toBe(true);
  });

  test('CC-38 clicking Adopt creates an ordinary pending change proposal', async ({ steward }) => {
    test.skip(!driftedFlow, 'PORTAL_DRIFTED_FLOW is unset — drift is produced out of band, not from the browser');
    const { page } = steward;

    // 1. Count pending approvals prior to adopt
    await openView(page, approvalsNav);
    const pendingItems = page.getByRole('article').or(page.getByRole('row'))
      .filter({ hasText: /pending approval|čaká na schválenie/i });
    const initialPendingCount = await pendingItems.count();

    // 2. Return to gallery and adopt drifted state
    await openView(page, galleryNav);
    const flowItem = page.getByRole('article').filter({ hasText: driftedFlow! })
      .or(page.getByRole('row', { name: new RegExp(driftedFlow!, 'i') }))
      .or(page.getByText(driftedFlow!).locator('..'))
      .first();
    await flowItem.getByRole('button', { name: /drifted|odchýlené|drift|resolve/i })
      .or(flowItem.getByText(/drifted|odchýlené|drift/i))
      .first().click();

    const dialog = page.getByRole('dialog')
      .or(page.locator('[data-testid="drift-actions"]'))
      .or(page.locator('.drift-resolution-actions'))
      .first();
    const adoptButton = dialog.getByRole('button', { name: /adopt|prevziať/i }).first();
    await expect(adoptButton, 'CC-38: adopt button must be enabled').toBeEnabled({ timeout: 15_000 });
    await adoptButton.click();

    // 3. Application must navigate to the newly generated proposal
    const proposalHeading = page.getByRole('heading', { name: new RegExp(driftedFlow!, 'i') })
      .or(page.getByText(new RegExp(driftedFlow!, 'i'))).first();
    await expect(proposalHeading, 'CC-38: adopting drift must route to the created change proposal').toBeVisible({ timeout: 25_000 });
    await expect(
      page.getByText(/pending approval|čaká na schválenie/i).first(),
      'CC-33: adopted change proposal must reflect "pending approval" state',
    ).toBeVisible({ timeout: 20_000 });

    // 4. Return to approvals list: count must have incremented by exactly 1
    await openView(page, approvalsNav);
    await expect.poll(async () => {
      return await page.getByRole('article').or(page.getByRole('row'))
        .filter({ hasText: /pending approval|čaká na schválenie/i })
        .count();
    }, {
      message: 'CC-38: approvals view must have exactly one more pending proposal after adoption',
      timeout: 30_000,
      intervals: [1_000, 2_000],
    }).toBe(initialPendingCount + 1);
  });

  test('CC-21 the adopted proposal displays the exported live state diff and remains pending', async ({ steward }) => {
    test.skip(!driftedFlow, 'PORTAL_DRIFTED_FLOW is unset — drift is produced out of band, not from the browser');
    const { page } = steward;
    await openView(page, approvalsNav);

    const proposalRow = page.getByRole('article').or(page.getByRole('row'))
      .filter({ hasText: driftedFlow! })
      .filter({ hasText: /pending approval|čaká na schválenie/i })
      .first();
    await expect(proposalRow, `CC-21: approvals list must show adopted proposal for "${driftedFlow}"`).toBeVisible({ timeout: 25_000 });

    await proposalRow.getByRole('button', { name: /review|open|detail|zobraziť/i })
      .or(proposalRow.getByRole('link', { name: /review|open|detail|zobraziť/i })).first().click();

    const diffRegion = page.getByRole('region', { name: /plan|diff|zmeny/i })
      .or(page.getByRole('heading', { name: /plan|diff|zmeny/i }).locator('..'))
      .or(page.locator('[data-testid="plan-diff"]'))
      .first();
    await expect(diffRegion, 'CC-21: adopted proposal must present the jcctl plan/diff view').toBeVisible({ timeout: 20_000 });
    await expect(
      diffRegion.getByText(new RegExp(driftedFlow!, 'i')).first(),
      'CC-21: plan diff must explicitly name the drifted resource being adopted',
    ).toBeVisible();

    // Must not be auto-applied or live without approver action
    const liveChip = page.getByText(/^\s*(live|nasadené)\s*$/i);
    expect(await liveChip.count(), 'CC-38: newly adopted proposal must not be live without approval').toBe(0);
  });

  test('CC-21 a converged flow does not offer drift resolution actions', async ({ steward }) => {
    test.skip(!liveFlow, 'PORTAL_LIVE_FLOW is unset — skipping non-drifted negative assertion');

    const { page } = steward;
    await openView(page, galleryNav);

    const liveItem = page.getByRole('article').filter({ hasText: liveFlow! })
      .or(page.getByRole('row', { name: new RegExp(liveFlow!, 'i') }))
      .or(page.getByText(liveFlow!).locator('..'))
      .first();
    await expect(liveItem, `CC-21: flow gallery must list live flow "${liveFlow}"`).toBeVisible({ timeout: 20_000 });

    const statusChip = liveItem.locator('[data-status], [data-testid="lifecycle-status"]')
      .or(liveItem.getByText(/live|nasadené/i))
      .first();
    await expect(statusChip, 'UI-25: converged flow must display its status chip').toBeVisible({ timeout: 15_000 });

    const chipText = (await statusChip.textContent()) || '';
    expect(chipText, `CC-21: converged flow status must not show drifted, got "${chipText}"`).not.toMatch(/drifted|odchýlené/i);

    // Negative assertion: Revert / Adopt action buttons must not be rendered for non-drifted resources
    const revertBtn = liveItem.getByRole('button', { name: /revert|vrátiť/i });
    const adoptBtn = liveItem.getByRole('button', { name: /adopt|prevziať/i });
    expect(await revertBtn.count(), 'CC-21: Revert action must not be offered for converged flow').toBe(0);
    expect(await adoptBtn.count(), 'CC-21: Adopt action must not be offered for converged flow').toBe(0);
  });
});

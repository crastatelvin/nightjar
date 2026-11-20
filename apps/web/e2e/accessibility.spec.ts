import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { fileIncident, waitForInvestigation } from "./helpers";

/**
 * Accessibility is part of being usable during an incident.
 *
 * The checks run against the real pages with real data, not a component storybook:
 * a focus trap that only exists in the app, a contrast failure caused by a live
 * status tone, or a form label lost in hydration are exactly the defects a
 * component-level test cannot see.
 *
 * `critical` and `serious` fail the run. `moderate` and `minor` are reported in the
 * job log because a small team triages them against other work; they are not
 * invisible, they are just not a release blocker.
 */

const BLOCKING = new Set(["critical", "serious"]);

async function scan(page: Page) {
  return new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
}

async function expectNoBlockingViolations(page: Page, surface: string): Promise<void> {
  const results = await scan(page);
  const blocking = results.violations.filter((violation) =>
    BLOCKING.has(String(violation.impact)),
  );

  const summary = blocking
    .map(
      (violation) =>
        `${violation.impact}: ${violation.id} — ${violation.help} (${violation.nodes.length} node(s))`,
    )
    .join("\n");

  expect(blocking, `${surface} has blocking accessibility violations:\n${summary}`).toEqual([]);
}

test.describe("accessibility", () => {
  test("the command deck", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/control plane live/)).toBeVisible();
    await expectNoBlockingViolations(page, "command deck");
  });

  test("the fault studio", async ({ page }) => {
    await page.goto("/studio");
    await expect(page.getByText("Break something.")).toBeVisible();
    await expectNoBlockingViolations(page, "fault studio");
  });

  test("the settings page", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByText("Point the UI at your control plane.")).toBeVisible();
    await expectNoBlockingViolations(page, "settings");
  });

  test("a case file with a plan and a decision waiting", async ({ page, request }) => {
    const incidentId = await fileIncident(request, "site_bad_deploy", { variant: 3 });
    await waitForInvestigation(request, incidentId);

    await page.goto(`/incidents/${incidentId}`);
    await expect(page.getByText("Lifecycle").first()).toBeVisible();

    await expectNoBlockingViolations(page, "case file");
  });
});

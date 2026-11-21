import { expect, test } from "@playwright/test";

import { fileIncident, waitForInvestigation } from "./helpers";

/**
 * Screenshots for review, not for comparison.
 *
 * Pixel diffing is deliberately not used here. The background is a canvas dust field
 * and the layout is dense with sub-pixel type; a committed snapshot would encode the
 * rasterizer, the GPU, and the desktop scale of whichever machine generated it, and a
 * small team ends up regenerating baselines until the check means nothing. Instead
 * the job captures full-page images of every surface and uploads them with the run, so
 * a reviewer can see what the change did - including at phone width - and the axe
 * spec (which does fail the build) covers the defects a person would otherwise be
 * looking for.
 *
 * `reducedMotion: "reduce"` is honoured by the app: the canvas renders one static
 * frame instead of animating, so the images are comparable between runs even though
 * they are not asserted on.
 */

const DESKTOP = [
  { route: "/", name: "command-deck" },
  { route: "/studio", name: "fault-studio" },
  { route: "/settings", name: "settings" },
];

const OUTPUT = "test-results/screenshots";

test.describe("visual", () => {
  test.use({ reducedMotion: "reduce" });

  for (const { route, name } of DESKTOP) {
    test(`${name} at desktop width`, async ({ page }) => {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      // The entry animations are finite; this is the settled composition.
      await page.waitForTimeout(900);
      await page.screenshot({ path: `${OUTPUT}/desktop-${name}.png`, fullPage: true });
    });
  }

  test("a case file at desktop width", async ({ page, request }) => {
    const incidentId = await fileIncident(request, "site_bad_deploy", { variant: 0 });
    await waitForInvestigation(request, incidentId);
    await page.goto(`/incidents/${incidentId}`);
    await expect(page.getByText("Lifecycle").first()).toBeVisible();
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${OUTPUT}/desktop-case-file.png`, fullPage: true });
  });

  test("the command deck and a case file at phone width", async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${OUTPUT}/mobile-command-deck.png`, fullPage: true });

    const incidentId = await fileIncident(request, "site_bad_deploy", { variant: 0 });
    await waitForInvestigation(request, incidentId);
    await page.goto(`/incidents/${incidentId}`);
    await expect(page.getByText("Lifecycle").first()).toBeVisible();
    await page.waitForTimeout(900);
    await page.screenshot({ path: `${OUTPUT}/mobile-case-file.png`, fullPage: true });
  });
});

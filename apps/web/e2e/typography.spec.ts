import { expect, test } from "@playwright/test";

/**
 * The interface must be legible without a network.
 *
 * Nightjar is self-hosted, and the host it runs on is frequently the one that just
 * had the incident: no egress, no font CDN, sometimes no proxy. A typeface pulled from
 * a third party is therefore two failures at once - it is a request that cannot
 * resolve, and it is telemetry that the operator did not ask for. The fonts are
 * vendored in `app/fonts/` and loaded through `next/font/local`, and these checks keep
 * it that way: the faces that render are ours, and no page asks another origin for
 * anything.
 */

const ROUTES = ["/", "/studio", "/settings"];

test.describe("typography", () => {
  test("renders in the bundled sans and mono families", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/control plane live/)).toBeVisible();

    const sans = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(sans.toLowerCase().startsWith("inter"), `body renders in ${sans}`).toBe(true);

    const mono = await page.evaluate(() => {
      const element = document.querySelector(".mono");
      return element ? getComputedStyle(element).fontFamily : "";
    });
    expect(mono.toLowerCase().startsWith("jetbrainsmono"), `mono renders in ${mono}`).toBe(
      true,
    );

    // `document.fonts` reports every CSS-connected face; `loaded` is the state that
    // proves the woff2 was actually served and parsed, not merely declared.
    const fonts = await page.evaluate(() =>
      Array.from(document.fonts).map((face) => ({
        family: face.family.toLowerCase(),
        status: face.status,
      })),
    );
    for (const family of ["inter", "jetbrainsmono"]) {
      expect(
        fonts.some((face) => face.family === family && face.status === "loaded"),
        `${family} should be loaded: ${JSON.stringify(fonts)}`,
      ).toBe(true);
    }
  });

  test("no page requests leave the host", async ({ page }) => {
    // The control plane and the lab are separate ports on the same machine, so the
    // rule is not "one origin" - it is "nothing off this host". A font CDN, an
    // analytics endpoint, or an error reporter would be the first public address to
    // appear here, and any of them is a build that cannot run air-gapped.
    const loopback = (hostname: string) =>
      hostname === "localhost" ||
      hostname === "::1" ||
      hostname === "[::1]" ||
      /^127\./.test(hostname);

    const foreign: string[] = [];
    const seen: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.protocol !== "http:" && url.protocol !== "https:") return;
      seen.push(url.href);
      if (!loopback(url.hostname)) foreign.push(url.href);
    });

    for (const route of ROUTES) {
      await page.goto(route);
      await page.waitForLoadState("networkidle");
    }

    expect(seen.length, "the observer should have seen the page load").toBeGreaterThan(0);
    expect([...new Set(foreign)]).toEqual([]);
  });
});

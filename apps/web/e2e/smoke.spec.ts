import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

/**
 * The four surfaces, checked against a live stack.
 *
 * These are smoke assertions, not a full interaction suite: the goal is to catch
 * the failures that a typecheck and a build cannot see — a route that throws on
 * hydration, a proxy that cannot reach the lab, a case file that renders empty,
 * or a stale snapshot that overwrites the stream.
 */

test.describe("command deck", () => {
  test("reports a live control plane and lists incidents", async ({ page }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", { name: /nothing is resolved until it is proven/i }),
    ).toBeVisible();

    // The header only says "live" once the health and meta probes succeed.
    await expect(page.getByText(/control plane live/)).toBeVisible();
    await expect(page.getByText("Incident stream")).toBeVisible();
    await expect(page.getByRole("link", { name: /simulate/i })).toBeVisible();
  });

  test("opens the command palette from the keyboard", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/control plane live/)).toBeVisible();

    await page.keyboard.press("Control+k");

    const palette = page.getByRole("dialog");
    await expect(palette).toBeVisible();
    await expect(palette.getByText("Navigate", { exact: true })).toBeVisible();
    await expect(palette.getByText("Fault studio")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
  });
});

test.describe("fault studio", () => {
  test("shows every scenario and a passing lab audit", async ({ page }) => {
    await page.goto("/studio");

    await expect(page.getByText("Break something.")).toBeVisible();

    for (const name of [
      "Poisoned CDN cache",
      "Bad deployment",
      "Checkout dependency down",
      "Leaked API key",
      "Reported phishing",
    ]) {
      await expect(page.getByRole("button", { name: new RegExp(name, "i") })).toBeVisible();
    }

    // The audit is independent of incident state: it asks the lab directly.
    await expect(page.getByText("Ground truth · lab audit", { exact: false })).toBeVisible();
    await expect(page.getByText("PASS").first()).toBeVisible();
  });
});

test.describe("case file", () => {
  const apiBase = process.env.NIGHTJAR_API_URL ?? "http://127.0.0.1:8000";

  /** Files a lab scenario through the web's demo proxy and returns its id. */
  async function fileIncident(
    request: APIRequestContext,
    scenario: string,
    variant: number,
  ): Promise<string> {
    const response = await request.post("/api/demo/run", {
      data: { scenario, variant },
    });
    expect(response.ok(), `demo proxy rejected ${scenario}`).toBeTruthy();
    const body = (await response.json()) as { incident?: { id?: string } };
    const incidentId = body.incident?.id;
    expect(incidentId, "the demo proxy should return the filed incident").toBeTruthy();
    return incidentId as string;
  }

  /**
   * Waits until intake and investigation have written their records, so the
   * assertions below are about rendering, not about how fast the mock model is.
   */
  async function waitForInvestigation(
    request: APIRequestContext,
    incidentId: string,
  ): Promise<void> {
    await expect
      .poll(
        async () => {
          const response = await request.get(`${apiBase}/v1/incidents/${incidentId}`);
          if (!response.ok()) return `http-${response.status()}`;
          const file = (await response.json()) as {
            incident?: { status?: string };
            findings?: unknown[];
          };
          const status = file.incident?.status ?? "unknown";
          const investigated =
            status !== "intake" && status !== "investigating" && (file.findings?.length ?? 0) > 0;
          return investigated ? "ready" : status;
        },
        { timeout: 30_000, intervals: [500, 1000, 2000] },
      )
      .toBe("ready");
  }

  test("renders the loop for a freshly filed incident", async ({ page, request }) => {
    const incidentId = await fileIncident(request, "site_cache_poisoned", 0);
    await waitForInvestigation(request, incidentId);

    await page.goto(`/incidents/${incidentId}`);

    await expect(page.getByText("Case file", { exact: true })).toBeVisible();
    await expect(page.getByText("Lifecycle").first()).toBeVisible();
    await expect(page.getByText("Assessment").first()).toBeVisible();
    await expect(page.getByText("Investigator findings").first()).toBeVisible();
    await expect(page.getByText("Verification", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Evidence ledger", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Integrity").first()).toBeVisible();

    // A case file that never leaves the skeleton state is a failed load, even
    // when every element above happens to be present.
    await expect(page.getByText(/confidence/i).first()).toBeVisible();
  });

  test("keeps the newest state after a stream update", async ({ page, request }) => {
    const incidentId = await fileIncident(request, "site_bad_deploy", 1);
    await waitForInvestigation(request, incidentId);

    await page.goto(`/incidents/${incidentId}`);
    await expect(page.getByText("Lifecycle").first()).toBeVisible();

    // The stream emits timeline events; the hook turns them into snapshots.
    // A note posted straight to the API must therefore appear without a reload.
    // This is the regression guard for a slower duplicate fetch reverting the
    // page to an older snapshot.
    const note = `smoke ${Date.now()}`;
    const noted = await request.post(`${apiBase}/v1/incidents/${incidentId}/notes`, {
      data: { body: note },
      headers: { "x-nightjar-actor": "playwright" },
      failOnStatusCode: false,
    });
    expect(noted.ok(), "the API should accept a note on a live incident").toBeTruthy();

    await expect(page.getByText(note, { exact: true })).toBeVisible({ timeout: 20_000 });
  });
});

test.describe("settings", () => {
  test("probes the control plane and shows its identity", async ({ page }) => {
    await page.goto("/settings");

    await expect(page.getByText("Point the UI at your control plane.")).toBeVisible();

    await page.getByRole("button", { name: /test connection/i }).click();

    await expect(page.getByText(/^Connected ·/)).toBeVisible();
    await expect(page.getByText("Control plane", { exact: true })).toBeVisible();
    await expect(page.getByText("Version")).toBeVisible();
  });
});

import { expect, test } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

import { apiBase, fileIncident, pendingApprovalId, waitForStatus } from "./helpers";

/**
 * The human gate and what happens when verification disagrees with it.
 *
 * These are the three interactions an operator cannot afford to take on trust: the
 * approval button actually starts execution, a fix that fails the stability window
 * reopens the case, and a rejected plan can be re-investigated without losing the
 * old one. The smoke spec proves the pages render; this one proves the loop moves.
 */

// A case has to be filed, investigated, approved, executed, and held for the
// stability window. That is longer than Playwright's default test budget, and the
// window is a product setting rather than a test knob, so the budget is raised
// instead of shortening the thing under test.
test.describe.configure({ timeout: 180_000 });

test.describe("approval", () => {
  test("approving a plan executes it and the case resolves on proof", async ({ page, request }) => {
    // A rollback is R2; the cache purge used elsewhere is R1 and never asks.
    const incidentId = await fileIncident(request, "site_bad_deploy", { variant: 0 });
    await waitForStatus(request, incidentId, ["awaiting_approval"]);

    await page.goto(`/incidents/${incidentId}`);
    await expect(page.getByText("Approval required", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Approve", exact: true }).click();

    // The toast is the immediate feedback; the case file follows the stream.
    await expect(page.getByRole("status").getByText(/Approved\. Execution starts now/)).toBeVisible();
    await expect(page.getByText(/^approved$/i).first()).toBeVisible({ timeout: 30_000 });

    await waitForStatus(request, incidentId, ["resolved"], { timeout: 120_000 });
    await page.reload();
    await expect(page.getByText("Resolved", { exact: true }).first()).toBeVisible();
  });

  test("a fix that fails the stability window reopens the case", async ({ page, request }) => {
    const incidentId = await fileIncident(request, "site_bad_deploy", {
      variant: 2,
      relapse: true,
    });
    await waitForStatus(request, incidentId, ["awaiting_approval"]);

    const approvalId = await pendingApprovalId(request, incidentId);
    const decided = await request.post(
      `${apiBase}/v1/incidents/${incidentId}/approvals/${approvalId}`,
      {
        data: { approved: true, comment: "relapse check" },
        headers: { "x-nightjar-actor": "playwright" },
      },
    );
    expect(
      decided.ok(),
      `the approval should be recorded (${decided.status()})`,
    ).toBeTruthy();

    // T+0 passes, the window runs, the lab flips the cache back, and the stability
    // check fails. That is the false-resolution guard doing its only job.
    const reopened = await waitForStatus(request, incidentId, ["reopened"], {
      timeout: 150_000,
    });
    expect(reopened).toBe("reopened");

    await page.goto(`/incidents/${incidentId}`);
    await expect(page.getByText("Reopened", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/stability window failed/i).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /new plan/i })).toBeVisible();
  });

  test("rejecting a plan stops execution, and a retry re-investigates", async ({ page, request }) => {
    const incidentId = await fileIncident(request, "site_bad_deploy", { variant: 1 });
    await waitForStatus(request, incidentId, ["awaiting_approval"]);

    await page.goto(`/incidents/${incidentId}`);
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(page.getByRole("status").getByText(/Rejected\. Nothing executes/)).toBeVisible();

    await waitForStatus(request, incidentId, ["failed"]);
    await page.reload();
    await expect(page.getByText("Failed", { exact: true }).first()).toBeVisible();

    // The failed case keeps its old plan and evidence; retrying opens a new
    // investigation rather than editing history.
    await page.getByRole("button", { name: /new plan/i }).click();
    await expect(
      page.getByRole("status").getByText(/New investigation started/),
    ).toBeVisible();

    await waitForStatus(request, incidentId, ["awaiting_approval"], { timeout: 120_000 });
    await page.reload();
    await expect(page.getByText("Approval required", { exact: true })).toBeVisible();
  });
});

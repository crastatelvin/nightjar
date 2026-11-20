import { expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

/**
 * Shared helpers for the lifecycle specs.
 *
 * These talk to the API the same way the UI does: incidents are filed through the
 * web app's demo proxy, and state is read from the case file. Nothing reaches into
 * the lab or the database, so a passing spec is evidence about the product rather
 * than about a test fixture.
 */

export const apiBase = process.env.NIGHTJAR_API_URL ?? "http://127.0.0.1:8000";

export async function fileIncident(
  request: APIRequestContext,
  scenario: string,
  options: { variant?: number; relapse?: boolean } = {},
): Promise<string> {
  const response = await request.post("/api/demo/run", {
    data: {
      scenario,
      variant: options.variant ?? 0,
      relapse: options.relapse === true,
    },
  });
  expect(response.ok(), `demo proxy rejected ${scenario}`).toBeTruthy();
  const body = (await response.json()) as { incident?: { id?: string } };
  const incidentId = body.incident?.id;
  expect(incidentId, "the demo proxy should return the filed incident").toBeTruthy();
  return incidentId as string;
}

export async function incidentStatus(
  request: APIRequestContext,
  incidentId: string,
): Promise<string> {
  const response = await request.get(`${apiBase}/v1/incidents/${incidentId}`);
  if (!response.ok()) return `http-${response.status()}`;
  const file = (await response.json()) as { incident?: { status?: string } };
  return file.incident?.status ?? "unknown";
}

/**
 * Wait for any of the listed statuses. A single status is usually enough, but the
 * pipeline moves between `intake`, `investigating`, and `awaiting_approval` fast
 * enough that a poll can miss the one in the middle.
 */
export async function waitForStatus(
  request: APIRequestContext,
  incidentId: string,
  accepted: string[],
  { timeout = 120_000 }: { timeout?: number } = {},
): Promise<string> {
  let last = "unknown";
  await expect
    .poll(
      async () => {
        last = await incidentStatus(request, incidentId);
        return accepted.includes(last);
      },
      { timeout, intervals: [500, 1000, 2000, 3000] },
    )
    .toBe(true);
  return last;
}

export async function waitForInvestigation(
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
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe("ready");
}

/** The pending approval's id, so a spec can decide through the API when the click is not the subject. */
export async function pendingApprovalId(
  request: APIRequestContext,
  incidentId: string,
): Promise<string> {
  const response = await request.get(`${apiBase}/v1/incidents/${incidentId}`);
  expect(response.ok(), "the case file should be readable").toBeTruthy();
  const body = (await response.json()) as {
    approvals?: Array<{ id: string; status: string }>;
  };
  const pending = (body.approvals ?? []).find((item) => item.status === "pending");
  expect(pending?.id, "an approval should be pending").toBeTruthy();
  return pending?.id as string;
}

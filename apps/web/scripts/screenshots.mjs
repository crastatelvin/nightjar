/**
 * Capture the README screenshots from the real UI.
 *
 * The launch page claims the loop is real, so the pictures on it have to be a
 * capture of this build against the lab connector, not a mockup. This script files
 * one incident per class through the API, approves it, waits for the chain to close,
 * and then photographs the pages an operator actually uses.
 *
 * Run it with the stack up (lab, API, web) and a freshly migrated database, so the
 * command deck counts only the cases this script filed:
 *
 *   npm run screenshots
 *
 * Environment:
 *   PLAYWRIGHT_BASE_URL   web app (default http://127.0.0.1:3001)
 *   NIGHTJAR_API_URL      control plane (default http://127.0.0.1:8000)
 *   NIGHTJAR_LAB_URL      lab (default http://127.0.0.1:8100)
 *   SHOTS_DIR             output directory (default ../../docs/assets)
 */

import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(process.env.SHOTS_DIR ?? resolve(here, "../../../docs/assets"));
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3001";
const apiURL = (process.env.NIGHTJAR_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const labURL = (process.env.NIGHTJAR_LAB_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");

const SEED_FIELDS = ["title", "description", "incident_class", "severity", "source", "metadata"];
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const step = (message) => console.log(`[shots] ${message}`);

async function api(path, init = {}) {
  const response = await fetch(`${apiURL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path} -> ${response.status}`);
  return response.json();
}

async function fileIncident(scenario, variant) {
  const response = await fetch(`${labURL}/lab/scenario/${scenario}?variant=${variant}`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`lab seed ${scenario} -> ${response.status}`);
  const seed = await response.json();
  const body = Object.fromEntries(SEED_FIELDS.map((field) => [field, seed[field]]));
  return api("/v1/incidents?wait=true", { method: "POST", body: JSON.stringify(body) });
}

async function waitForStatus(id, statuses, timeout = 180_000) {
  const deadline = Date.now() + timeout;
  let last = "";
  while (Date.now() < deadline) {
    const caseFile = await api(`/v1/incidents/${id}`);
    last = caseFile.incident.status;
    if (statuses.includes(last)) return caseFile;
    await sleep(700);
  }
  throw new Error(`incident ${id} stuck in ${last}`);
}

async function approveAll(id) {
  const approvals = await api(`/v1/incidents/${id}/approvals`);
  for (const approval of approvals) {
    if (approval.status !== "pending") continue;
    await api(`/v1/incidents/${id}/approvals/${approval.id}`, {
      method: "POST",
      body: JSON.stringify({ approved: true, comment: "approved while capturing screenshots" }),
    });
  }
}

async function runToResolution(scenario, variant) {
  const incident = await fileIncident(scenario, variant);
  await waitForStatus(incident.id, ["awaiting_approval", "resolved"]);
  await approveAll(incident.id);
  const caseFile = await waitForStatus(incident.id, ["resolved"]);
  step(`${scenario} resolved with ${caseFile.chain.evidence_checked} evidence entries`);
  return incident.id;
}

/** Hide the dev-only chrome so the pictures match a production build. */
const CLEAN = () => {
  const style = document.createElement("style");
  style.textContent = `
    nextjs-portal { display: none !important; }
    [data-nextjs-toast] { display: none !important; }
    #nj-demo-cursor, #nj-demo-caption, #nj-demo-ripple { display: none !important; }
  `;
  document.head.appendChild(style);
};

const main = async () => {
  await mkdir(outDir, { recursive: true });
  // SHOTS_ONLY=studio,settings re-captures a subset without filing incidents, which is
  // what you want after a copy change on one page.
  const only = (process.env.SHOTS_ONLY ?? "")
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean);
  const want = (name) => only.length === 0 || only.includes(name);
  const needsIncident = want("case-file") || want("verification");

  let outageId = null;
  if (needsIncident) {
    await fetch(`${labURL}/lab/reset`, { method: "POST" });
    step("filing the outage case and stopping at the decision");
    const outage = await fileIncident("site_bad_deploy", 1);
    await waitForStatus(outage.id, ["awaiting_approval", "resolved"]);
    outageId = outage.id;
  }

  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 2,
      reducedMotion: "no-preference",
      colorScheme: "dark",
    });
    await context.addInitScript(CLEAN);
    const page = await context.newPage();

    const shoot = async (name, options = {}) => {
      await sleep(600);
      await page.screenshot({ path: `${outDir}/${name}.png`, ...options });
      step(`wrote ${name}.png`);
    };

    if (want("case-file")) {
      step("case file, waiting on a human");
      await page.goto(`${baseURL}/incidents/${outageId}`, { waitUntil: "networkidle" });
      await page.getByText("Lifecycle").first().waitFor({ timeout: 30_000 });
      await page
        .getByRole("button", { name: "Approve", exact: true })
        .waitFor({ timeout: 60_000 });
      await page.mouse.move(1300, 880);
      await shoot("case-file");
    }

    if (want("verification")) {
      step("approving and waiting for the proof");
      await approveAll(outageId);
      const resolved = await waitForStatus(outageId, ["resolved"]);
      await page.goto(`${baseURL}/incidents/${outageId}`, { waitUntil: "networkidle" });
      const verification = page.getByText("VERIFICATION", { exact: false }).first();
      await verification.waitFor({ timeout: 60_000 });
      await verification.scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy({ top: -140 }));
      await sleep(900);
      await shoot("verification");
      step(
        `outage closed: ${resolved.verifications.filter((v) => v.passed).length} predicates passed`,
      );
    }

    if (want("dashboard")) {
      if (needsIncident) {
        step("filing the credential and phishing cases for the deck counters");
        await runToResolution("credential_leaked", 3);
        await runToResolution("phishing_report", 4);
      }
      step("command deck");
      await page.goto(baseURL, { waitUntil: "networkidle" });
      await page.getByText("Nothing is resolved").first().waitFor({ timeout: 30_000 });
      await sleep(1600);
      await page.mouse.move(1300, 880);
      await shoot("dashboard");
    }

    if (want("studio")) {
      step("fault studio");
      await page.goto(`${baseURL}/studio`, { waitUntil: "networkidle" });
      await page.getByRole("heading", { name: /break something/i }).waitFor({ timeout: 30_000 });
      await page.getByRole("button", { name: /Bad deployment/ }).click();
      await sleep(1200);
      await page.mouse.move(1300, 880);
      await shoot("studio");
    }

    if (want("settings")) {
      step("connection settings");
      await page.goto(`${baseURL}/settings`, { waitUntil: "networkidle" });
      await sleep(1600);
      await page.mouse.move(1300, 880);
      await shoot("settings");
    }
  } finally {
    await browser.close();
  }
};

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

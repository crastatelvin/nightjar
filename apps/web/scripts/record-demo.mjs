/**
 * Record the three-class walkthrough that lives at `docs/assets/demo.mp4`.
 *
 * This is a build script, not a test: it drives the real UI against a running lab,
 * control plane, and web app, and writes a video of what an operator sees. The test
 * suite proves behaviour; this makes it watchable, which is the one thing a launch
 * page cannot get from a screenshot.
 *
 * Run it with the stack up and the browsers installed:
 *
 *   npm run demo:record
 *
 * Environment:
 *   PLAYWRIGHT_BASE_URL      web app (default http://127.0.0.1:3001)
 *   PLAYWRIGHT_BROWSERS_PATH where the Chromium build lives, if not the default
 *
 * Lab state is reset at the start (DEMO_KEEP_LAB=1 keeps it). The command deck reads
 * the control plane's database rather than the lab, so record a published capture
 * against a freshly migrated database; otherwise the counters and the decision queue
 * still show every incident the dev machine has ever filed.
 *
 * The raw capture is deliberately written at 1x and 1280x720. The anvil background is
 * a particle field over film grain, which compresses badly; `scripts/encode-demo.sh`
 * is the second pass that turns the capture into the committed asset.
 */

import { mkdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../../docs/assets/demo-raw.webm");
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3001";
const labURL = (process.env.NIGHTJAR_LAB_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");
const videoDir = join(here, "../.demo-video");

/**
 * A pointer, click ripples, and a caption, so the recording reads without a voice-over.
 *
 * The pointer is purely cosmetic. Playwright dispatches the clicks; this layer only
 * reports where one is about to land, because moving the real mouse across a tilt card
 * changes the card's transform and makes a hand-rolled mousedown/mouseup pair land on
 * a node that React replaced a frame earlier. Reliable input, decorative pointer.
 */
const OVERLAY = () => {
  const style = document.createElement("style");
  style.textContent = `
    #nj-demo-cursor {
      position: fixed; left: 0; top: 0; width: 20px; height: 20px; margin: -10px 0 0 -10px;
      border-radius: 999px; border: 1.5px solid rgba(200, 255, 114, 0.92);
      background: rgba(200, 255, 114, 0.16); box-shadow: 0 0 18px rgba(200, 255, 114, 0.45);
      pointer-events: none; z-index: 2147483647; opacity: 0;
      transition: opacity 180ms ease, left 260ms cubic-bezier(0.22, 1, 0.36, 1),
        top 260ms cubic-bezier(0.22, 1, 0.36, 1);
    }
    #nj-demo-cursor.on { opacity: 1; }
    #nj-demo-ripple {
      position: fixed; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 999px;
      border: 1.5px solid rgba(200, 255, 114, 0.75); pointer-events: none;
      z-index: 2147483646; animation: nj-demo-pop 560ms ease-out forwards;
    }
    @keyframes nj-demo-pop {
      from { transform: scale(1); opacity: 0.9; }
      to   { transform: scale(4.4); opacity: 0; }
    }
    #nj-demo-caption {
      position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%) translateY(10px);
      opacity: 0; padding: 9px 17px; border-radius: 999px;
      border: 1px solid rgba(200, 255, 114, 0.26); background: rgba(5, 7, 12, 0.88);
      backdrop-filter: blur(10px); color: #e9eff8; white-space: nowrap;
      font: 500 12.5px/1.2 Inter, system-ui, sans-serif; letter-spacing: 0.01em;
      z-index: 2147483645; pointer-events: none;
      transition: opacity 320ms ease, transform 320ms ease;
    }
    #nj-demo-caption.on { opacity: 1; transform: translateX(-50%) translateY(0); }
  `;

  const mount = () => {
    if (!document.body) {
      requestAnimationFrame(mount);
      return;
    }
    document.head.appendChild(style);

    const cursor = document.createElement("div");
    cursor.id = "nj-demo-cursor";
    document.body.appendChild(cursor);

    const caption = document.createElement("div");
    caption.id = "nj-demo-caption";
    document.body.appendChild(caption);

    window.__njDemoPoint = (x, y) => {
      cursor.classList.add("on");
      cursor.style.left = `${x}px`;
      cursor.style.top = `${y}px`;
    };

    window.__njDemoTap = () => {
      const ripple = document.createElement("div");
      ripple.id = "nj-demo-ripple";
      ripple.style.left = cursor.style.left;
      ripple.style.top = cursor.style.top;
      document.body.appendChild(ripple);
      window.setTimeout(() => ripple.remove(), 620);
    };

    let timer;
    window.__njDemoCaption = (text) => {
      window.clearTimeout(timer);
      caption.textContent = text ?? "";
      caption.classList.toggle("on", Boolean(text));
      if (!text) return;
      timer = window.setTimeout(() => caption.classList.remove("on"), 5200);
    };
  };

  mount();
};

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const step = (message) => console.log(`[demo] ${message}`);

async function caption(page, text) {
  await page.evaluate((value) => window.__njDemoCaption?.(value), text);
}

/** Glide the decorative pointer to a target and let it settle. */
async function aim(page, locator, timeout = 30_000) {
  await locator.waitFor({ state: "visible", timeout });
  await locator.scrollIntoViewIfNeeded({ timeout });
  const box = await locator.boundingBox();
  if (!box) throw new Error("element has no box");
  await page.evaluate(
    ([x, y]) => window.__njDemoPoint?.(x, y),
    [box.x + box.width / 2, box.y + box.height / 2],
  );
  await sleep(320);
}

async function tap(page, locator, settle = 600) {
  await aim(page, locator);
  await page.evaluate(() => window.__njDemoTap?.());
  await locator.click({ timeout: 30_000 });
  // The real pointer is what drives hover states and native tooltips, and Playwright
  // leaves it parked on whatever it just clicked. Move it off the navigation chrome so
  // a "Command deck" tooltip does not sit in the closing shot.
  await page.mouse.move(640, 706);
  await sleep(settle);
}

async function read(page, distance = 420, settle = 900) {
  await page.evaluate((dy) => window.scrollBy({ top: dy, behavior: "smooth" }), distance);
  await sleep(settle);
}

/** One class of incident, followed from injection to proof. */
async function runScenario(page, { card, text }) {
  step(`scenario: ${card}`);

  // The case file repeats the nav in its footer, so scope to the first match.
  await tap(page, page.getByRole("link", { name: "Fault studio" }).first(), 500);
  await page.getByRole("heading", { name: /break something/i }).waitFor({ timeout: 30_000 });
  await sleep(900);

  await caption(page, text);
  const cardButton = page.getByRole("button", { name: new RegExp(card) });
  await tap(page, cardButton, 750);
  if ((await cardButton.getAttribute("aria-pressed")) !== "true") {
    throw new Error(`the ${card} card did not become the selected scenario`);
  }

  await tap(page, page.getByRole("button", { name: /inject fault and respond/i }), 500);

  // The studio hands back the filed incident as soon as triage runs; the case file is
  // then where the loop actually happens.
  await tap(page, page.getByRole("link", { name: /open case file/i }), 1200);
  await page.getByText("Lifecycle").first().waitFor({ timeout: 30_000 });
  await sleep(700);
  await read(page, 320);

  const approveButton = page.getByRole("button", { name: "Approve", exact: true });
  await approveButton.waitFor({ timeout: 120_000 });
  await caption(page, "R2 change: the loop stops until a human decides");
  await sleep(1500);
  await tap(page, approveButton, 900);

  await caption(page, "Executed, then re-checked at T+0 and again after the stability window");
  await read(page, 460);
  await page.getByText("Resolved", { exact: true }).first().waitFor({ timeout: 180_000 });
  await sleep(1500);
  step(`${card}: resolved`);
  await read(page, 360, 1100);
  await caption(page, "Resolved because the proof held — not because an agent said so");
  await sleep(2400);
  await caption(page, "");
}

const main = async () => {
  // The deck counts what the lab has seen, so a recording on a lab full of test runs
  // opens on a wall of old incidents. Start from zero unless the caller wants the state.
  if (process.env.DEMO_KEEP_LAB !== "1") {
    const reset = await fetch(`${labURL}/lab/reset`, { method: "POST" });
    if (!reset.ok) throw new Error(`lab reset failed (${reset.status})`);
    step("lab reset");
  }

  await rm(videoDir, { recursive: true, force: true });
  await mkdir(videoDir, { recursive: true });
  await mkdir(dirname(output), { recursive: true });

  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 720 },
      reducedMotion: "no-preference",
      recordVideo: { dir: videoDir, size: { width: 1280, height: 720 } },
    });
    await context.addInitScript(OVERLAY);
    const page = await context.newPage();
    const video = page.video();
    if (!video) throw new Error("video recording is not available in this context");

    step("command deck");
    await page.goto(baseURL, { waitUntil: "networkidle" });
    await page.evaluate(() => window.__njDemoPoint?.(640, 360));
    await sleep(2200);
    await caption(page, "Nightjar — incident response that proves it fixed the problem");
    await sleep(2400);
    await page.evaluate(() => window.scrollBy({ top: 380, behavior: "smooth" }));
    await sleep(2000);

    await runScenario(page, {
      card: "Bad deployment",
      text: "Site outage — a bad deploy, rolled back and re-verified",
    });
    await runScenario(page, {
      card: "Leaked API key",
      text: "Exposed credential — revoked, then proven dead",
    });
    await runScenario(page, {
      card: "Reported phishing",
      text: "Phishing — quarantined and blocked, both read back",
    });

    step("closing shot");
    await tap(page, page.getByRole("link", { name: "Command deck" }).first(), 600);
    // The deck fetches its counters when it mounts, so arriving from the last case file
    // would capture the snapshot from before that case resolved. Reload once, then hold.
    await page.reload({ waitUntil: "networkidle" });
    await page.evaluate(() => window.__njDemoPoint?.(640, 320));
    await sleep(1400);
    await caption(page, "Three classes. One loop. Every resolution carries a proof.");
    await sleep(3600);

    await context.close();
    await video.saveAs(output);
    await rm(videoDir, { recursive: true, force: true });
    step(`wrote ${output}`);
  } finally {
    // Leaving the browser open keeps the recorder alive and the node process with it -
    // a failed run would hang instead of reporting the failure.
    await browser.close().catch(() => {});
  }
};

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

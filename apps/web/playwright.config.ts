import { defineConfig, devices } from "@playwright/test";

/**
 * Visual smoke test for the case-file UI.
 *
 * It runs against an already-running stack: the lab, the API, and the web app.
 * That keeps the test honest (it exercises the real SSE stream, the real demo
 * proxy, and the real API) and lets CI reuse the services it already starts.
 *
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:3001 npm run test:e2e
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["github"]] : [["list"]],
  timeout: 45_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 900 } },
    },
  ],
});

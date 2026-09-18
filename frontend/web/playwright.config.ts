import { defineConfig } from "@playwright/test";

import { API_URL, WEB_PORT } from "./e2e/stack";

// Browser end-to-end tests (T-315): `pnpm -F web e2e`. Needs Postgres (make dev) and the Python
// venv; the tests start their own API and worker (e2e/stack.ts). The web app is a production
// build in .next-e2e pointing at the e2e API, so it does not disturb the dev server.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    // Locally the installed Google Chrome; CI installs Playwright's Chromium.
    channel: process.env.CI ? undefined : "chrome",
    // CI runners have no GPU: allow Chromium's software WebGL (SwiftShader) for the office tests.
    launchOptions: process.env.CI ? { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] } : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `pnpm exec next build && pnpm exec next start -p ${WEB_PORT}`,
    url: `http://localhost:${WEB_PORT}/dashboard`,
    env: { NEXT_PUBLIC_API_URL: API_URL, NEXT_DIST_DIR: ".next-e2e" },
    timeout: 300_000,
    reuseExistingServer: false,
    stdout: "ignore",
  },
});

import { defineConfig, devices } from "@playwright/test";

// Runs against a stack that is already up (`make up`, or a staging deployment):
//   E2E_APP_URL (default http://localhost:3000), E2E_API_URL (default http://localhost:8000).
// The global setup creates a fresh organization and owner through the backend CLI (E2E_COMPOSE=0 skips it and
// uses E2E_EMAIL / E2E_PASSWORD / E2E_ORG instead, e.g. against staging).
export default defineConfig({
  testDir: "./tests",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./global-setup.ts",
  use: {
    baseURL: process.env.E2E_APP_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    launchOptions: process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {},
  },
});

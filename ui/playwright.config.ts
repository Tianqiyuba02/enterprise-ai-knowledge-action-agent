import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] }, testIgnore: /mobile\.spec\.ts/ },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], browserName: "chromium" },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: "node e2e/mock-backend.mjs",
      url: "http://127.0.0.1:4010/__health",
      reuseExistingServer: false,
    },
    {
      command: "npm run build && npm run start -- --hostname 127.0.0.1 --port 3100",
      url: "http://127.0.0.1:3100/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        BACKEND_URL: "http://127.0.0.1:4010",
        INTERNAL_PORTAL_KEY: "deterministic-e2e-only",
        VISITOR_COOKIE_SECRET: "deterministic-e2e-cookie-secret",
      },
    },
  ],
});

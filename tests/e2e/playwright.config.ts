/**
 * Playwright configuration for deephaven-plugin-tones E2E tests.
 *
 * Two test groups:
 *   - tests/tier1/  — Component-level browser tests of ToneEngine via built bundle.
 *                     MUST pass.  No Deephaven server required.  Uses the
 *                     harness static server (port 19876).
 *   - tests/tier2/  — Full IDE integration tests.  Best-effort.  Most tests
 *                     are .skip'd because full IDE automation is fragile; they
 *                     are provided as reference implementations.  Requires the
 *                     Deephaven server on port 10000 with PSK tonestest123.
 *
 * Run commands (from /workspace/tests/e2e/):
 *   npm test                # all tests (tier1 + tier2)
 *   npm run test:tier1      # tier1 only (MUST pass gate)
 *   npm run test:tier2      # tier2 only
 */

import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "url";

export default defineConfig({
  // Look for tests in the tests/ subdirectory
  testDir: "./tests",

  // Run tests in files in parallel
  fullyParallel: false,

  // Fail the build on CI if you accidentally left test.only in the source code
  forbidOnly: !!process.env.CI,

  // Retry on CI only
  retries: process.env.CI ? 2 : 0,

  // Single worker to avoid audio context conflicts between tests
  workers: 1,

  // Reporter to use
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
  ],

  use: {
    // Default browser: Chromium (headless)
    ...devices["Desktop Chrome"],

    // Collect trace when retrying the failed test
    trace: "on-first-retry",

    // Screenshot on failure
    screenshot: "only-on-failure",

    // Longer timeout for audio operations
    actionTimeout: 10_000,
  },

  projects: [
    {
      name: "tier1",
      testMatch: "**/tier1/**/*.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        // Launch args needed for headless audio (Chromium)
        launchOptions: {
          args: [
            "--autoplay-policy=no-user-gesture-required",
            "--disable-web-security",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--use-file-for-fake-audio-capture=/dev/null",
          ],
        },
      },
    },
    {
      name: "tier2",
      testMatch: "**/tier2/**/*.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: {
          args: [
            "--autoplay-policy=no-user-gesture-required",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
          ],
        },
      },
    },
  ],

  // Harness static server — required for tier1 tests.
  // Playwright starts this before the tests run and tears it down after.
  webServer: {
    command: "node harness/server.js 19876",
    url: "http://127.0.0.1:19876/",
    timeout: 30_000,
    reuseExistingServer: !process.env.CI,
    cwd: fileURLToPath(new URL(".", import.meta.url)),
    stdout: "pipe",
    stderr: "pipe",
  },

  // Global test timeout
  timeout: 60_000,

  // Output dir for test artifacts
  outputDir: "test-results",
});

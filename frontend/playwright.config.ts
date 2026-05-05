import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E configuration for the frontend.
 *
 * Tests cover:
 * - Layout: sidebar collapse/expand, workspace panel resize
 * - Preview: Excel sticky header, PDF/Word/HTML containment
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3080',
    trace: 'on-first-retry',
    viewport: { width: 1280, height: 720 },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3080',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});

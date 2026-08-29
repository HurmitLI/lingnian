import { defineConfig, devices } from "@playwright/test";

const publicBaseUrl = process.env.DEMO_E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  testMatch: "demo-access.spec.ts",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: publicBaseUrl ?? "http://127.0.0.1:3022",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "邀请体验·桌面 Chrome", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    {
      name: "邀请体验·手机 Chrome",
      use: {
        ...devices["Desktop Chrome"],
        channel: "chrome",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: publicBaseUrl ? undefined : {
    command: "../scripts/start_demo_e2e.sh",
    url: "http://127.0.0.1:3022/demo-login",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});

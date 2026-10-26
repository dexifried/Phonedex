import { defineConfig, devices } from "@playwright/test";
import { platform } from 'node:os';
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8794",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
  webServer: {
    command: platform()==='win32' ? "..\\.venv\\Scripts\\python.exe tests\\serve.py" : "../.venv/bin/python tests/serve.py",
    url: "http://127.0.0.1:8794/api/auth/status",
    reuseExistingServer: false,
    timeout: 30000,
  },
});

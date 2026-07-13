import path from "node:path"
import { defineConfig, devices } from "@playwright/test"

const projectRoot = path.resolve(process.cwd(), "..")

export default defineConfig({
  testDir: "./tests/fullstack",
  timeout: 45_000,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3000",
    channel: "chromium",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
      cwd: path.join(projectRoot, "backend"),
      env: {
        ...process.env,
        CHALK_WEB_ENV: "test",
        CHALK_SESSION_SECRET: "fullstack-test-session-secret",
        CHALK_WEB_DATA_DIR: path.join(projectRoot, "data", "e2e"),
        CHALK_CORS_ORIGINS: "http://127.0.0.1:3000",
      },
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "corepack pnpm start --hostname 127.0.0.1 --port 3000",
      cwd: path.join(projectRoot, "frontend"),
      env: {
        ...process.env,
        NEXT_PUBLIC_CHALK_API_BASE: "http://127.0.0.1:8000/api",
      },
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})

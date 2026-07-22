import path from "node:path"
import fs from "node:fs"
import crypto from "node:crypto"
import { defineConfig, devices } from "@playwright/test"

const projectRoot = path.resolve(process.cwd(), "..")
const backendPort = Number(process.env.CHALK_FULLSTACK_BACKEND_PORT ?? "8000")
const frontendPort = Number(process.env.CHALK_FULLSTACK_FRONTEND_PORT ?? "3000")
const backendBaseUrl = `http://127.0.0.1:${backendPort}`
const frontendBaseUrl = `http://127.0.0.1:${frontendPort}`

function ensureScience125ContextFixture() {
  const target = path.join(projectRoot, "data", "e2e", "science125", "science125-context-v1.json")
  fs.mkdirSync(path.dirname(target), { recursive: true })
  const manifest = JSON.parse(fs.readFileSync(path.join(projectRoot, "benchmarks", "science125", "science125-v1.json"), "utf8")) as {
    questions: Array<{ id: string; question: string; pdfPage: number; bookletPage: number }>
  }
  const items = manifest.questions.map((item) => {
    const sourceContext = `${item.question} Deterministic full-stack test context for ${item.id}.`
    const canonical = sourceContext.normalize("NFC").trim().replace(/\s+/g, " ")
    return {
      id: item.id,
      headline: item.question,
      sourceContext,
      contextSha256: crypto.createHash("sha256").update(canonical, "utf8").digest("hex"),
      pdfPage: item.pdfPage,
      bookletPage: item.bookletPage,
    }
  })
  fs.writeFileSync(target, JSON.stringify({
    indexVersion: "science125-context-v1",
    manifestVersion: "science125-v1",
    sourcePdfSha256: "4bda50e8e3c90f8968f1bfd72ded4d9587ae80cd40ba66656a12c93abcf8e576",
    sourcePdfFilename: "sjtu-booklet.pdf",
    extractionVersion: "pymupdf-block-anchor-v1",
    items,
  }), "utf8")
}

ensureScience125ContextFixture()

export default defineConfig({
  testDir: "./tests/fullstack",
  timeout: 45_000,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: frontendBaseUrl,
    channel: "chromium",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `python -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: path.join(projectRoot, "backend"),
      env: {
        ...process.env,
        CHALK_WEB_ENV: "test",
        CHALK_SESSION_SECRET: "fullstack-test-session-secret",
        CHALK_WEB_DATA_DIR: path.join(projectRoot, "data", "e2e"),
        CHALK_CORS_ORIGINS: frontendBaseUrl,
      },
      url: `${backendBaseUrl}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `corepack pnpm build && corepack pnpm start --hostname 127.0.0.1 --port ${frontendPort}`,
      cwd: path.join(projectRoot, "frontend"),
      env: {
        ...process.env,
        NEXT_PUBLIC_CHALK_API_BASE: `${backendBaseUrl}/api`,
      },
      url: frontendBaseUrl,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})

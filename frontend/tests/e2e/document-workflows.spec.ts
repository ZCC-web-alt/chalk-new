import { expect, test, type Page, type Route } from "@playwright/test"

type DocumentRecord = {
  id: number
  title: string
  sourceType: string
  fileName: string | null
  summary: string | null
  createdAt: string
}

function job(id: string, type: string, status: string, result: unknown = null, progress = 0) {
  return {
    id,
    type,
    status,
    progress,
    message: status === "SUCCEEDED" ? "Completed." : "Running.",
    result,
    error: null,
    feedbackPrompt: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  }
}

async function mockSession(page: Page) {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 1, username: "test-user", createdAt: "2026-01-01T00:00:00Z" } }),
  }))
  await page.route("**/api/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok" }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 10, totalItems: 0, totalPages: 0 } }),
  }))
}

async function fulfillDocuments(route: Route, documents: DocumentRecord[]) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: documents, pagination: { page: 1, pageSize: 100, totalItems: documents.length, totalPages: documents.length ? 1 : 0 } }),
  })
}

test("uploads a PDF job and refreshes the real document list", async ({ page }) => {
  await mockSession(page)
  let documents: DocumentRecord[] = []
  await page.route("**/api/documents**", async (route) => {
    if (route.request().method() === "POST" && route.request().url().endsWith("/documents/import")) {
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("import-1", "pdf_import", "QUEUED")) })
      return
    }
    await fulfillDocuments(route, documents)
  })
  await page.route("**/api/jobs/import-1", async (route) => {
    documents = [{ id: 11, title: "paper.pdf", sourceType: "pdf", fileName: "paper.pdf", summary: null, createdAt: "2026-01-01T00:00:00Z" }]
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(job("import-1", "pdf_import", "SUCCEEDED", { documentId: 11, title: "paper.pdf", chunkCount: 2 }, 100)) })
  })

  await page.goto("/")
  await page.getByLabel("选择 PDF 文件").setInputFiles({ name: "paper.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF") })
  await expect(page.getByText("paper.pdf", { exact: true }).first()).toBeVisible()
})

test("runs literature search and renders normalized API results", async ({ page }) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => fulfillDocuments(route, []))
  await page.route("**/api/jobs", async (route) => {
    const payload = route.request().postDataJSON()
    expect(payload.type).toBe("literature_search")
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("search-1", "literature_search", "QUEUED")) })
  })
  await page.route("**/api/jobs/search-1", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("search-1", "literature_search", "SUCCEEDED", {
      results: [{ id: "r1", title: "Contract result", authors: "A. Author", journal: "Journal", year: "2025", doi: "10.1000/result", abstract: "", sourcePlatform: "crossref", url: "https://example.test/result", isOpenAccess: true, relevanceScore: 0.8, accessStatus: "metadata_only", needsFulltext: true, warning: "" }],
      platformStatus: { crossref: { status: "ok", count: 1 } },
      warnings: [],
      query: {},
    }, 100)),
  }))

  await page.goto("/")
  await page.getByRole("button", { name: "文献检索" }).click()
  await page.locator("textarea").fill("contract query")
  await page.getByRole("button", { name: "开始检索" }).click()
  await expect(page.getByText("Contract result")).toBeVisible()
})

test("runs RAG QA and renders only returned answer and citations", async ({ page }, testInfo) => {
  await mockSession(page)
  const documents: DocumentRecord[] = [{ id: 7, title: "Owned document", sourceType: "pdf", fileName: "owned.pdf", summary: null, createdAt: "2026-01-01T00:00:00Z" }]
  await page.route("**/api/documents**", (route) => fulfillDocuments(route, documents))
  await page.route("**/api/jobs", async (route) => {
    const payload = route.request().postDataJSON()
    expect(payload.type).toBe("rag_qa")
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("qa-1", "rag_qa", "QUEUED")) })
  })
  await page.route("**/api/jobs/qa-1", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("qa-1", "rag_qa", "SUCCEEDED", {
      answer: "Answer returned by the API.",
      chunks: [],
      citations: [{ index: 1, documentId: 7, chunkId: 3, sourceTitle: "Owned document", excerpt: "Source excerpt", score: 0.9, contextMarkers: "" }],
    }, 100)),
  }))

  await page.goto("/")
  await page.getByRole("button", { name: "智能问答" }).click()
  await page.getByPlaceholder("输入问题").fill("What does the document say?")
  await page.getByRole("button", { name: "发送" }).click()
  await expect(page.getByText("Answer returned by the API.")).toBeVisible()
  await expect(page.locator("p:visible").filter({ hasText: "Source excerpt" })).toBeVisible()
  if (process.env.CHALK_VISUAL_QA === "1") {
    await page.screenshot({ path: testInfo.outputPath("rag-answer.png"), fullPage: true })
  }
})

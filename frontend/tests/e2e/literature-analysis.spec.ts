import { expect, test, type Page, type Route } from "@playwright/test"

type DocumentRecord = {
  id: number
  title: string
  sourceType: string
  fileName: string | null
  summary: string | null
  createdAt: string
}

const documents: DocumentRecord[] = [
  { id: 7, title: "First owned paper", sourceType: "pdf", fileName: "first.pdf", summary: null, createdAt: "2026-01-01T00:00:00Z" },
  { id: 8, title: "Second owned paper", sourceType: "pdf", fileName: "second.pdf", summary: null, createdAt: "2026-01-02T00:00:00Z" },
]

function analysis(id: string, type: string, documentIds: number[], result: Record<string, unknown>) {
  return {
    id,
    type,
    documentIds,
    result,
    jobId: `job-${id}`,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  }
}

function job(id: string, type: string, status: string, result: unknown = null, resource: Record<string, unknown> | null = null) {
  return {
    id,
    type,
    status,
    progress: status === "SUCCEEDED" ? 100 : 20,
    message: status === "SUCCEEDED" ? "Completed." : "Running.",
    result,
    error: null,
    feedbackPrompt: null,
    resource,
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
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
}

async function fulfillDocuments(route: Route) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: documents, pagination: { page: 1, pageSize: 100, totalItems: 2, totalPages: 1 } }),
  })
}

test("generates and displays a persisted full-document summary", async ({ page }) => {
  await mockSession(page)
  await page.route(/\/api\/documents\?/, fulfillDocuments)
  await page.route("**/api/documents/7/analyses", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }))
  await page.route("**/api/jobs", async (route) => {
    const payload = route.request().postDataJSON()
    expect(payload).toEqual({ type: "document_analysis", payload: { documentId: 7, analysisType: "summary" } })
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify(job("summary-job", "document_analysis", "QUEUED", null, { documentId: 7, analysisType: "summary" })),
    })
  })
  const summary = analysis("summary-1", "summary", [7], {
    summary: "Summary returned by the analysis API.",
    segmentCount: 3,
    sourceCharCount: 18000,
  })
  await page.route("**/api/jobs/summary-job", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("summary-job", "document_analysis", "SUCCEEDED", summary, { documentId: 7, analysisType: "summary" })),
  }))

  await page.goto("/")
  await page.getByRole("button", { name: "生成摘要" }).click()
  await expect(page.getByText("Summary returned by the analysis API.")).toBeVisible()
  await expect(page.getByText("18,000 字符")).toBeVisible()
})

test("renders persisted analysis tabs and connects lab record and glossary actions", async ({ page }, testInfo) => {
  await mockSession(page)
  await page.route(/\/api\/documents\?/, fulfillDocuments)
  await page.route("**/api/documents/7/analyses", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      data: [
        analysis("images-1", "images", [7], { images: [{ assetId: "asset-1", fileName: "figure.png", mimeType: "image/png" }], count: 1, usedLlmPageHints: false }),
        analysis("structures-1", "structures", [7], { chemicals: ["ethanol"], compounds: [{ name: "ethanol", molecularFormula: "C2H6O", molecularWeight: "46.07", assetId: "asset-2" }], warnings: [] }),
        analysis("safety-1", "safety", [7], { chemicals: [{ name: "ethanol", source: "local", status: "ok", signalWord: "Danger", ghsCodes: ["H225"], pictograms: ["Flame"], hazards: ["Highly flammable"], highRisk: true, warning: "" }], highRiskNames: ["ethanol"], warnings: [] }),
        analysis("sop-1", "sop", [7], { title: "Owned synthesis", chemicals: [], steps: [{ step: 1, action: "Stir", params: "1 h", safetyNote: "" }], postProcessing: "Filter", characterization: "XRD" }),
        analysis("reactions-1", "reactions", [7], { summary: "One reaction", reactions: [{ name: "Hydrogenation", reactants: ["A", "H2"], products: ["B"], catalyst: "Pd/C", solvent: "ethanol", temperature: "25 C", time: "2 h", pressure: "1 bar", ph: "", yield: "90%", workup: "filter", notes: "" }] }),
        analysis("translation-1", "translation", [7], { segments: [{ index: 1, sourceText: "Source paragraph", translation: "译文段落" }], glossary: [{ en: "overpotential", zh: "过电位", note: "" }], segmentCount: 1, sourceCharCount: 16 }),
      ],
    }),
  }))
  await page.route("**/api/documents/7/assets/*", (route) => route.fulfill({
    status: 200,
    contentType: "image/png",
    body: Buffer.from("iVBORw0KGgo=", "base64"),
  }))
  let labRecordCreated = false
  await page.route("**/api/lab-records", async (route) => {
    labRecordCreated = true
    const payload = route.request().postDataJSON()
    expect(payload.title).toBe("Hydrogenation")
    expect(payload.relatedDocIds).toBe("[7]")
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: 1, ...payload, aiSuggestion: null, createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z" }) })
  })
  let glossarySaved = false
  await page.route("**/api/glossary/batch", async (route) => {
    glossarySaved = true
    expect(route.request().postDataJSON().terms[0]).toEqual({ enTerm: "overpotential", zhTerm: "过电位", note: "" })
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [], count: 1 }) })
  })

  await page.goto("/")
  await page.getByRole("button", { name: "结构式" }).click()
  await expect(page.getByText("C2H6O")).toBeVisible()
  await page.getByRole("button", { name: "安全" }).click()
  await expect(page.getByText("Highly flammable")).toBeVisible()
  await page.getByRole("button", { name: "SOP" }).click()
  await expect(page.getByText("Owned synthesis")).toBeVisible()
  await page.getByRole("button", { name: "反应" }).click()
  await page.getByRole("button", { name: "生成实验记录" }).click()
  await expect.poll(() => labRecordCreated).toBe(true)
  await page.getByRole("button", { name: "翻译" }).click()
  await expect(page.getByText("译文段落")).toBeVisible()
  await page.getByRole("button", { name: "保存到术语库" }).click()
  await expect.poll(() => glossarySaved).toBe(true)
  if (process.env.CHALK_VISUAL_QA === "1") {
    await page.screenshot({ path: testInfo.outputPath("translation-workspace.png"), fullPage: true })
  }
})

test("compares two selected documents and renders API markdown", async ({ page }, testInfo) => {
  await mockSession(page)
  await page.route(/\/api\/documents\?/, fulfillDocuments)
  await page.route("**/api/documents/7/analyses", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [] }) }))
  await page.route(/\/api\/document-comparisons\/latest.*/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: null }) }))
  await page.route("**/api/jobs", (route) => route.fulfill({
    status: 202,
    contentType: "application/json",
    body: JSON.stringify(job("compare-job", "document_compare", "QUEUED", null, { documentIds: [7, 8] })),
  }))
  const comparison = analysis("compare-1", "comparison", [7, 8], {
    markdown: "| 文献 | 结论 |\n| --- | --- |\n| First owned paper | API comparison result |",
    documents: documents.map((document) => ({ id: document.id, title: document.title })),
    sourceCharCounts: { "7": 100, "8": 120 },
  })
  await page.route("**/api/jobs/compare-job", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("compare-job", "document_compare", "SUCCEEDED", comparison, { documentIds: [7, 8] })),
  }))

  await page.goto("/")
  await page.getByLabel("选择 First owned paper 用于对比").click()
  await page.getByLabel("选择 Second owned paper 用于对比").click()
  await page.getByRole("button", { name: "对比分析" }).click()
  await expect(page.getByText("API comparison result")).toBeVisible()
  if (process.env.CHALK_VISUAL_QA === "1") {
    await page.screenshot({ path: testInfo.outputPath("comparison-dialog.png"), fullPage: true })
  }
})

import { expect, test, type Page } from "@playwright/test"

const pagination = { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 }

function job(id: string, type: string, status: string, result: unknown = null) {
  return {
    id, type, status, result,
    progress: status === "SUCCEEDED" ? 100 : 25,
    message: status === "SUCCEEDED" ? "Completed." : "Running.",
    error: null, feedbackPrompt: null, resource: null,
    createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
  }
}

async function mockSession(page: Page) {
  await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { id: 1, username: "test-user", createdAt: "2026-01-01T00:00:00Z" } }) }))
  await page.route("**/api/health", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [], pagination }) }))
  await page.route("**/api/multimodal/runs**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [], pagination }) }))
}

test("moves selected search results into evidence and hypothesis input", async ({ page }) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [], pagination }) }))
  let evidencePayload: Record<string, unknown> | null = null
  await page.route("**/api/evidence/literature", async (route) => {
    evidencePayload = route.request().postDataJSON()
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ count: 1 }) })
  })
  await page.route("**/api/jobs", (route) => route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("search-finish", "literature_search", "QUEUED")) }))
  await page.route("**/api/jobs/search-finish", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(job("search-finish", "literature_search", "SUCCEEDED", {
      results: [{ id: "r1", title: "Selected real result", authors: "A. Author", journal: "Journal", year: "2025", doi: "10.1000/selected", abstract: "Measured evidence.", sourcePlatform: "Crossref", url: "https://example.test", isOpenAccess: true, relevanceScore: 0.9, accessStatus: "open_access", needsFulltext: false, warning: "" }],
      platformStatus: { crossref: { status: "ok", count: 1 } }, warnings: [], query: {},
    })),
  }))

  await page.goto("/")
  await page.getByRole("button", { name: "文献检索" }).click()
  await page.locator("textarea").fill("selected query")
  await page.getByRole("button", { name: "开始检索" }).click()
  await page.getByLabel("选择检索结果 Selected real result").check()
  await page.getByRole("button", { name: "加入证据库" }).click()
  await expect.poll(() => evidencePayload).not.toBeNull()
  expect((evidencePayload as unknown as { references: Array<{ title: string }> }).references[0].title).toBe("Selected real result")
  await page.getByRole("button", { name: "加入假设输入" }).click()
  await expect(page.getByText("假设生成", { exact: true }).last()).toBeVisible()
  await expect(page.getByText("Selected real result")).toBeVisible()
})

test("reimports a selected PDF through the persistent document job", async ({ page }) => {
  await mockSession(page)
  const document = { id: 7, title: "Owned paper", sourceType: "pdf", fileName: "owned.pdf", summary: null, createdAt: "2026-01-01T00:00:00Z" }
  let reimported = false
  await page.route(/\/api\/documents\?/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [document], pagination: { ...pagination, totalItems: 1, totalPages: 1 } }) }))
  await page.route("**/api/documents/7/analyses", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [] }) }))
  await page.route("**/api/documents/7/reimport", async (route) => {
    reimported = true
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("reimport-finish", "pdf_reimport", "QUEUED")) })
  })
  await page.route("**/api/jobs/reimport-finish", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(job("reimport-finish", "pdf_reimport", "SUCCEEDED", { documentId: 7, title: "Owned paper", chunkCount: 2 })) }))

  await page.goto("/")
  await page.getByLabel("选择重新导入的 PDF").setInputFiles({ name: "replacement.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF") })
  await expect.poll(() => reimported).toBe(true)
  await expect(page.getByText("Owned paper", { exact: true }).first()).toBeVisible()
})

test("saves a lab record before generating and displaying an AI suggestion", async ({ page }) => {
  await mockSession(page)
  const document = { id: 7, title: "Related paper", sourceType: "pdf", fileName: "paper.pdf", summary: "Summary", createdAt: "2026-01-01T00:00:00Z" }
  const record = { id: 3, title: "Owned run", content: "Observed heat", relatedDocIds: null, aiSuggestion: null, createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z" }
  let savedPayload: Record<string, unknown> | null = null
  await page.route("**/api/documents**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [document], pagination: { ...pagination, totalItems: 1, totalPages: 1 } }) }))
  await page.route("**/api/lab-records?pageSize=100*", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [record], pagination: { ...pagination, totalItems: 1, totalPages: 1 } }) }))
  await page.route("**/api/lab-records/3", async (route) => {
    savedPayload = route.request().postDataJSON()
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...record, ...savedPayload }) })
  })
  await page.route("**/api/jobs", (route) => route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("lab-finish", "lab_suggest", "QUEUED")) }))
  await page.route("**/api/jobs/lab-finish", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(job("lab-finish", "lab_suggest", "SUCCEEDED", { recordId: 3, suggestion: "Stop heating and verify the temperature probe." })) }))

  await page.goto("/")
  await page.getByRole("button", { name: "实验记录" }).click()
  await page.getByLabel("关联文献").selectOption("7")
  await page.getByRole("button", { name: "生成建议" }).click()
  await expect(page.getByText("Stop heating and verify the temperature probe.")).toBeVisible()
  await expect.poll(() => savedPayload).not.toBeNull()
  expect((savedPayload as unknown as { relatedDocIds: string }).relatedDocIds).toBe("[7]")
})

test("renders the real evidence relationship graph and imports curated evidence", async ({ page }) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [], pagination }) }))
  let imported = false
  let manifestUploaded = false
  await page.route("**/api/evidence/curated", async (route) => {
    imported = true
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ literatureEvidence: 1, domainEvidence: 1, imported: 2, updated: 0, skipped: 0 }) })
  })
  await page.route("**/api/evidence/manifests", async (route) => {
    manifestUploaded = true
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("manifest-finish", "evidence_manifest_import", "QUEUED")) })
  })
  await page.route("**/api/jobs/manifest-finish", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(job("manifest-finish", "evidence_manifest_import", "SUCCEEDED", { imported: 1, skipped: 0, errors: [], fileName: "manifest.csv" })) }))
  await page.route(/\/api\/evidence\?.*/, (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      data: [{ evidenceUid: "LIT-1", kind: "literature", displayTitle: "Evidence node", materialSystem: "NiFe", reactionType: "OER", keyData: "10 mA cm-2", reliabilityLevel: "verified" }],
      pagination: { ...pagination, totalItems: 1, totalPages: 1 },
      graph: { nodes: [{ id: "LIT-1", label: "Evidence node", kind: "literature" }, { id: "DOM-1", label: "Activity metric", kind: "domain" }], edges: [{ source: "LIT-1", target: "DOM-1", label: "supports" }] },
    }),
  }))

  await page.goto("/")
  await page.getByRole("button", { name: "证据数据库" }).click()
  await expect(page.getByRole("img", { name: "证据关系图" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Activity metric" })).toBeVisible()
  await page.getByRole("button", { name: "导入公开证据" }).click()
  await expect.poll(() => imported).toBe(true)
  await expect(page.getByText("当前包含 2 条可审计公开证据")).toBeVisible()
  await page.getByLabel("选择证据 manifest").setInputFiles({ name: "manifest.csv", mimeType: "text/csv", buffer: Buffer.from("material_system,reaction_context\nNiFe,OER\n") })
  await expect.poll(() => manifestUploaded).toBe(true)
  await expect(page.getByText("已导入 1 条计算证据，跳过 0 条")).toBeVisible()
})

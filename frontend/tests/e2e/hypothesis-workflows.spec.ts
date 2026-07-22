import { expect, test, type Page } from "@playwright/test"


function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "hypothesis-1",
    type: "hypothesis_generate",
    status: "QUEUED",
    stage: null,
    progress: 0,
    message: "",
    result: null,
    error: null,
    feedbackPrompt: null,
    resource: { sourceDocumentIds: [7], domain: "materials_chemistry" },
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

function detail(): Record<string, unknown> {
  return {
    id: 21,
    title: "Returned hypothesis",
    researchQuestion: "What should be tested?",
    confidence: 8,
    feasibility: "high",
    iterationCount: 2,
    sourceDocumentIds: [7],
    domain: "materials_chemistry",
    tags: ["reviewed"],
    status: "draft",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    hypothesis: { paperTitle: "Returned hypothesis", problemStatement: "A real API result" },
    sourceDocuments: [{ id: 7, title: "Owned paper" }],
    iterations: [{ round: 1, summary: "Iteration returned by API" }],
    critiqueHistory: [{ score: 8, summary: "Review returned by API" }],
    reasoningChain: { steps: [{ claim: "Reasoning returned by API" }] },
    debateHistory: [],
    hitl: { feedbackRecords: [{ mode: "hitl", interaction: { interactions: [] } }] },
    verification: { verificationReport: { status: "ok" } },
    scientificEvidence: { status: "ok" },
    workflow: { summary: { atomate2Workflows: 0 } },
    artifacts: [],
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
}

test("completes HITL review and renders native detail", async ({ page }) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      data: [{ id: 7, title: "Owned paper", sourceType: "pdf", fileName: "owned.pdf", summary: null, createdAt: "2026-01-01T00:00:00Z" }],
      pagination: { page: 1, pageSize: 100, totalItems: 1, totalPages: 1 },
    }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 10, totalItems: 0, totalPages: 0 } }),
  }))

  let state: "waiting" | "succeeded" = "waiting"
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback()
    const payload = route.request().postDataJSON()
    expect(payload.type).toBe("hypothesis_generate")
    expect(payload.payload.sourceDocIds).toEqual([7])
    expect(payload.payload.hitlEnabled).toBe(true)
    expect(payload.payload.autoVerify).toBe(true)
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job()) })
  })
  await page.route("**/api/jobs/hypothesis-1/feedback", async (route) => {
    expect(route.request().postDataJSON().action).toBe("approve")
    state = "succeeded"
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(job({ status: "RUNNING", stage: "HITL_FEEDBACK_RECEIVED", progress: 55 })),
    })
  })
  await page.route("**/api/jobs/hypothesis-1", async (route) => {
    if (state === "waiting") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(job({
          status: "WAITING_FOR_FEEDBACK",
          stage: "HITL_REVIEW",
          progress: 42,
          feedbackPrompt: {
            kind: "hypothesis_review",
            round: 0,
            stage: "initial",
            hypothesis: { paperTitle: "Draft returned by API", problemStatement: "Review this" },
            critique: {},
            debate: {},
            evidenceSummary: { status: "ok" },
            reviewContext: {},
            history: { interactions: [] },
          },
        })),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(job({
        status: "SUCCEEDED",
        stage: "completed",
        progress: 100,
        result: { hypothesisId: 21, summary: { title: "Returned hypothesis", confidence: 8, feasibility: "high", status: "draft" }, warnings: [], artifacts: [] },
      })),
    })
  })
  await page.route("**/api/hypotheses/21", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(detail()),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="hypothesis"]').click()
  await page.getByTestId("hypothesis-question").fill("What should be tested?")
  await page.getByRole("checkbox", { name: "Owned paper" }).click()
  await page.getByTestId("start-hypothesis").click()
  await expect(page.getByTestId("hypothesis-review-dialog")).toBeVisible()
  await expect(page.getByText("Draft returned by API")).toBeVisible()
  await page.getByTestId("approve-hypothesis").click()
  await expect(page.getByTestId("hypothesis-detail")).toContainText("A real API result")
})

test("restores a waiting HITL review after page refresh", async ({ page }) => {
  await mockSession(page)
  const waiting = job({
    status: "WAITING_FOR_FEEDBACK",
    stage: "HITL_REVIEW",
    progress: 42,
    feedbackPrompt: {
      kind: "hypothesis_review",
      round: 1,
      stage: "iteration",
      hypothesis: { paperTitle: "Restored review" },
      critique: {},
      debate: {},
      evidenceSummary: {},
      reviewContext: {},
      history: { interactions: [] },
    },
  })
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [waiting], pagination: { page: 1, pageSize: 10, totalItems: 1, totalPages: 1 } }),
  }))
  await page.route("**/api/jobs/hypothesis-1", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(waiting),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="hypothesis"]').click()
  await expect(page.getByTestId("hypothesis-review-dialog")).toContainText("Restored review")
})

test("report preview iframe has no same-origin permission", async ({ page }) => {
  await mockSession(page)
  const summary = {
    id: 21,
    title: "Returned hypothesis",
    researchQuestion: "What should be tested?",
    confidence: 8,
    feasibility: "high",
    iterationCount: 2,
    sourceDocumentIds: [7],
    domain: "materials_chemistry",
    tags: ["reviewed"],
    status: "reviewed",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  }
  const hypothesisDetail = detail()
  hypothesisDetail.status = "reviewed"
  hypothesisDetail.artifacts = [{
    id: "report-1",
    kind: "interactive_report_html",
    fileName: "report.html",
    mimeType: "text/html",
    sizeBytes: 100,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  }]
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route(/\/api\/hypotheses\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [summary], pagination: { page: 1, pageSize: 100, totalItems: 1, totalPages: 1 } }),
  }))
  let metadataPatch: Record<string, unknown> | null = null
  await page.route("**/api/hypotheses/21", (route) => {
    if (route.request().method() === "PATCH") {
      metadataPatch = route.request().postDataJSON()
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...summary, ...metadataPatch }) })
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(hypothesisDetail) })
  })
  await page.route("**/api/hypotheses/21/artifacts/report-1", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<html><body>Report returned by API</body></html>",
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="hyplib"]').click()
  await expect(page.getByText("Returned hypothesis").first()).toBeVisible()
  await page.getByTestId("edit-hypothesis").click()
  await page.getByTestId("hypothesis-edit-title").fill("Updated title")
  await page.getByTestId("hypothesis-edit-status").selectOption("approved")
  await page.getByTestId("hypothesis-edit-tags").fill("validated, stable")
  await page.getByTestId("save-hypothesis-metadata").click()
  await expect.poll(() => metadataPatch).toEqual({
    title: "Updated title",
    status: "approved",
    tags: ["validated", "stable"],
  })
  await page.getByTestId("preview-hypothesis-report").click()
  const frame = page.getByTestId("hypothesis-report-frame")
  await expect(frame).toBeVisible()
  await expect(frame).toHaveAttribute("sandbox", /allow-scripts/)
  await expect(frame).not.toHaveAttribute("sandbox", /allow-same-origin/)
})

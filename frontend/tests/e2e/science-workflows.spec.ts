import { expect, test, type Page } from "@playwright/test"


async function mockSession(page: Page) {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 1, username: "science-user", createdAt: "2026-01-01T00:00:00Z" } }),
  }))
  await page.route("**/api/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok" }),
  }))
}

function job(type: string, overrides: Record<string, unknown> = {}) {
  return {
    id: `${type}-job`,
    type,
    status: "QUEUED",
    stage: null,
    progress: 0,
    message: "",
    result: null,
    error: null,
    feedbackPrompt: null,
    resource: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

test("uploads a real multimodal resource and renders the persisted run", async ({ page }, testInfo) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 10, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/multimodal/assets?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/multimodal/runs?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))

  const asset = {
    id: "asset-1",
    fileName: "measurements.csv",
    mimeType: "text/csv",
    assetType: "workbook",
    metadata: { sheetNames: ["CSV"], sheetCount: 1, rows: 2, columns: 2, sizeBytes: 20 },
    sha256: "abc",
    createdAt: "2026-01-01T00:00:00Z",
  }
  await page.route("**/api/multimodal/assets", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ data: [asset] }) })
      return
    }
    await route.fallback()
  })
  const run = {
    id: "run-1",
    question: "",
    options: { useLiteratureContext: true },
    sources: [{ sourceType: "upload", assetId: "asset-1" }],
    revision: 1,
    jobId: "multimodal_analyze-job",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    result: {
      summary: { total: 1, succeeded: 1, failed: 0, dataPoints: 1, associations: 0 },
      items: [{
        status: "succeeded",
        source: { sourceType: "upload", assetId: "asset-1", fileName: "measurements.csv", mimeType: "text/csv" },
        imageType: "table",
        analysisMarkdown: "## Analysis\n\nMeasured value returned by API.",
        summary: "Measured value returned by API.",
        dataPoints: [{ parameter: "activity", value: 1.2, unit: "A" }],
        coverage: [{ sheetName: "CSV", rowsRead: 2, rowsAnalyzed: 2, strategy: "full" }],
        warnings: [],
        annotations: [],
      }],
      associations: [],
      quantitative: { scalingRelations: [], correlations: [] },
      context: "context",
      evidence: {},
      warnings: [],
    },
  }
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback()
    const payload = route.request().postDataJSON()
    expect(payload.type).toBe("multimodal_analyze")
    expect(payload.payload.sources[0]).toEqual({ sourceType: "upload", assetId: "asset-1", sheetNames: ["CSV"] })
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("multimodal_analyze")) })
  })
  await page.route("**/api/jobs/multimodal_analyze-job", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("multimodal_analyze", { status: "SUCCEEDED", progress: 100, result: { runId: "run-1" } })),
  }))
  await page.route("**/api/multimodal/runs/run-1", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(run),
  }))
  await page.route("**/api/multimodal/assets/asset-1/content?*", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      assetId: "asset-1",
      sheetName: "CSV",
      columns: ["sample", "value"],
      rows: [["A", "1.2"], ["B", "1.4"]],
      pagination: { page: 1, pageSize: 50, totalItems: 2, totalPages: 1 },
      coverage: { rowsRead: 2, columnsRead: 2, columnsTruncated: false, strategy: "paged-read-only-preview" },
    }),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="multimodal"]').click()
  await expect(page.getByText("尚未运行多模态分析").first()).toBeVisible()
  await page.locator('input[type="file"][accept*=".csv"]').setInputFiles({
    name: "measurements.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("x,y\n1,2\n"),
  })
  await expect(page.getByText("measurements.csv").first()).toBeVisible()
  await page.getByRole("button", { name: "开始分析" }).click()
  await expect(page.getByText("Measured value returned by API.")).toBeVisible()
  await expect(page.getByText("1/1", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: "表格", exact: true }).click()
  await expect(page.getByRole("cell", { name: "A" })).toBeVisible()
  await expect(page.getByRole("cell", { name: "1.4" })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath("multimodal-workspace.png"), fullPage: true })
})

test("selects at most ten workbook sheets and submits the explicit selection", async ({ page }) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 10, totalItems: 0, totalPages: 0 } }),
  }))
  const sheetNames = Array.from({ length: 11 }, (_, index) => `Sheet ${index + 1}`)
  await page.route("**/api/multimodal/assets?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      data: [{
        id: "workbook-1",
        fileName: "experiment.xlsx",
        mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        assetType: "workbook",
        metadata: { sheetNames, sheetCount: sheetNames.length, sizeBytes: 1024 },
        sha256: "abc",
        createdAt: "2026-01-01T00:00:00Z",
      }],
      pagination: { page: 1, pageSize: 100, totalItems: 1, totalPages: 1 },
    }),
  }))
  await page.route("**/api/multimodal/runs?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  let submittedSheets: string[] = []
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback()
    submittedSheets = route.request().postDataJSON().payload.sources[0].sheetNames
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("multimodal_analyze")) })
  })
  await page.route("**/api/jobs/multimodal_analyze-job", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("multimodal_analyze", {
      status: "FAILED",
      progress: 100,
      error: { code: "TEST_STOP", message: "Stopped after request capture." },
    })),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="multimodal"]').click()
  await page.getByRole("checkbox", { name: "选择 experiment.xlsx" }).click()
  await expect(page.getByRole("checkbox", { name: "Sheet 11" })).toBeDisabled()
  await page.getByRole("checkbox", { name: "Sheet 1", exact: true }).click()
  await page.getByRole("checkbox", { name: "Sheet 11", exact: true }).click()
  await page.getByRole("button", { name: "开始分析" }).click()

  await expect.poll(() => submittedSheets).toEqual([
    "Sheet 2", "Sheet 3", "Sheet 4", "Sheet 5", "Sheet 6",
    "Sheet 7", "Sheet 8", "Sheet 9", "Sheet 10", "Sheet 11",
  ])
})

test("clears document image selections when the selected document changes", async ({ page }) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      data: [
        { id: 1, title: "Paper A", sourceType: "pdf", originalFileName: "a.pdf", summary: "", createdAt: "2026-01-01T00:00:00Z" },
        { id: 2, title: "Paper B", sourceType: "pdf", originalFileName: "b.pdf", summary: "", createdAt: "2026-01-01T00:00:00Z" },
      ],
      pagination: { page: 1, pageSize: 100, totalItems: 2, totalPages: 1 },
    }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 10, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/multimodal/assets?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/multimodal/runs?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/documents/1/multimodal-sources", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      data: [{ id: "figure-a", documentId: 1, fileName: "figure-a.png", mimeType: "image/png" }],
      extractionStatus: "ready",
    }),
  }))
  await page.route("**/api/documents/2/multimodal-sources", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      data: [{ id: "figure-b", documentId: 2, fileName: "figure-b.png", mimeType: "image/png" }],
      extractionStatus: "ready",
    }),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="multimodal"]').click()
  const documentSelect = page.getByLabel("文献", { exact: true })
  await documentSelect.selectOption("1")
  await expect(page.getByText("figure-a.png")).toBeVisible()
  await page.getByText("figure-a.png").locator("xpath=ancestor::div[contains(@class, 'items-center')]").getByRole("checkbox").check()
  await expect(page.getByRole("button", { name: "开始分析" })).toBeEnabled()

  await documentSelect.selectOption("2")

  await expect(page.getByText("figure-b.png")).toBeVisible()
  await expect(page.getByRole("button", { name: "开始分析" })).toBeDisabled()
})

test("renders an editable modeling workspace and a nonblank structure canvas", async ({ page }, testInfo) => {
  await mockSession(page)
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/hypotheses**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 10, totalItems: 0, totalPages: 0 } }),
  }))
  await page.route("**/api/modeling/options", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      calcTypes: [{ id: "relax", name: "Geometry Optimization" }],
      vaspkitTasks: [{ id: "geometry_optimization", name: "Geometry Optimization" }],
      limits: { manualTextChars: 60000, structureBytes: 10485760 },
    }),
  }))
  await page.route("**/api/modeling/workspaces?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
  const workspace = {
    id: "workspace-1",
    source: { type: "manual", chars: 20 },
    mode: "vasp",
    revision: 1,
    activeStructureId: "structure-1",
    jobId: "modeling_generate-job",
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    originalResult: {},
    revisions: [],
    artifacts: [],
    structures: [{ id: "structure-1", workspaceId: "workspace-1", fileName: "POSCAR", format: "poscar", warnings: [], createdAt: "2026-01-01T00:00:00Z" }],
    result: {
      mode: "vasp",
      calcType: "relax",
      systemName: "Si",
      functional: "PBE",
      isMetal: false,
      incar: { ENCUT: { value: 520, source: "default", warnings: [] } },
      kpoints: { mode: "Gamma", mesh: [6, 6, 6], source: "default" },
      potcarElements: [
        { element: "Si", potential: "Si", source: "manual", warnings: [] },
        { element: "O", potential: "O", source: "default", warnings: [] },
      ],
      files: { incar: "ENCUT = 520\n", kpoints: "K-points\n", potcarGuide: "Si -> Si\n" },
      coverage: { sourceChars: 20, includedChars: 20, truncated: false },
      riskNotices: ["Dry-run only."],
    },
  }
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback()
    const payload = route.request().postDataJSON()
    expect(payload.type).toBe("modeling_generate")
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(job("modeling_generate")) })
  })
  await page.route("**/api/jobs/modeling_generate-job", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(job("modeling_generate", { status: "SUCCEEDED", progress: 100, result: { workspaceId: "workspace-1", revision: 1, coverage: {}, warnings: [] } })),
  }))
  let savedPotcar: Array<Record<string, unknown>> = []
  await page.route("**/api/modeling/workspaces/workspace-1", (route) => {
    if (route.request().method() === "PATCH") {
      savedPotcar = route.request().postDataJSON().changes.potcarElements
      const updated = {
        ...workspace,
        revision: 2,
        result: { ...workspace.result, potcarElements: savedPotcar },
        revisions: [{ id: "revision-2", revision: 2, changes: { potcarElements: savedPotcar }, diff: [], createdAt: "2026-01-01T00:01:00Z" }],
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(updated) })
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(workspace) })
  })
  await page.route("**/api/modeling/workspaces/workspace-1/structures/structure-1", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      ...workspace.structures[0],
      geometry: {
        formula: "Si",
        density: 2.3,
        cell: [[5.43, 0, 0], [0, 5.43, 0], [0, 0, 5.43]],
        lattice: { a: 5.43, b: 5.43, c: 5.43, alpha: 90, beta: 90, gamma: 90, volume: 160 },
        atoms: [{ index: 0, element: "Si", fractional: [0, 0, 0], cartesian: [0, 0, 0] }],
        bonds: [],
      },
    }),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="modeling"]').click()
  await page.getByLabel("计算方法或建模描述").fill("VASP PBE geometry optimization")
  await page.getByRole("button", { name: "生成工作区" }).click()
  await expect(page.getByText("Si · v1")).toBeVisible()
  await expect(page.getByLabel("INCAR ENCUT")).toHaveValue("520")
  await page.getByLabel("POTCAR Si potential").fill("Si_sv")
  await page.getByRole("button", { name: "下移 Si" }).click()
  await page.getByRole("button", { name: "保存参数" }).click()
  await expect.poll(() => savedPotcar.map((value) => `${value.element}:${value.potential}`)).toEqual(["O:O", "Si:Si_sv"])
  await page.getByRole("button", { name: "结构", exact: true }).click()
  const structureInput = page.locator('input[type="file"][data-structure-upload]')
  await expect(structureInput).not.toHaveAttribute("accept", /.+/)
  await expect(structureInput).toHaveAttribute("data-supported-files", /POSCAR/)
  const canvas = page.getByTestId("structure-canvas").locator("canvas")
  await expect(canvas).toBeVisible()
  await expect.poll(async () => canvas.evaluate((element) => {
    const value = (element as HTMLCanvasElement).toDataURL("image/png")
    return value.length
  })).toBeGreaterThan(1000)
  const beforeRotation = await canvas.evaluate((element) => (element as HTMLCanvasElement).toDataURL("image/png"))
  const bounds = await canvas.boundingBox()
  expect(bounds).not.toBeNull()
  if (bounds) {
    await page.mouse.move(bounds.x + bounds.width * 0.65, bounds.y + bounds.height * 0.5)
    await page.mouse.down()
    await page.mouse.move(bounds.x + bounds.width * 0.35, bounds.y + bounds.height * 0.35, { steps: 12 })
    await page.mouse.up()
  }
  await expect.poll(async () => canvas.evaluate((element) => (element as HTMLCanvasElement).toDataURL("image/png"))).not.toBe(beforeRotation)
  await canvas.hover()
  const beforeZoom = await canvas.evaluate((element) => (element as HTMLCanvasElement).toDataURL("image/png"))
  await page.mouse.wheel(0, -500)
  await expect.poll(async () => canvas.evaluate((element) => (element as HTMLCanvasElement).toDataURL("image/png"))).not.toBe(beforeZoom)
  await page.screenshot({ path: testInfo.outputPath("modeling-workspace.png"), fullPage: true })
})

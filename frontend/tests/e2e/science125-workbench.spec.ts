import { expect, test, type Page } from "@playwright/test"

const question = {
  id: "S125-006",
  question: "How can we measure interface phenomena on the microscopic level?",
  questionZh: "我们如何在微观尺度上测量界面现象？",
  sourceContext: "Interfacial chemistry studies molecular gas-liquid and liquid-solid interfaces. Optical interference and nanoscale film-thickness measurement can reveal microscopic transport phenomena.",
  sourceContextSha256: "context-hash-s125-006",
  extractionVersion: "science125-context-v1",
  sourceDomain: "Chemistry",
  benchmarkDomain: "Chemistry",
  primarySubdomain: "chem.interface",
  crossDomainTags: ["physics", "materials"],
  methodProfile: { primary: "experimental", secondary: ["computational"] },
  promptProfile: "s125.chemistry.v1",
  retrievalProfile: "retrieval.chem.interface.v1",
  classificationReviewStatus: "reviewed",
  pdfPage: 8,
  bookletPage: 6,
}

const completedSearch = {
  id: "science125-search-current",
  type: "literature_search",
  status: "SUCCEEDED",
  stage: "completed",
  progress: 100,
  message: "completed",
  error: null,
  feedbackPrompt: null,
  resource: { science125Id: question.id },
  result: {
    results: [{
      id: "result-current",
      title: "Interface measurement study",
      authors: "A. Researcher",
      journal: "Open Science",
      year: "2026",
      doi: "10.0000/interface",
      abstract: "A public literature result.",
      sourcePlatform: "Crossref",
      providerFamily: "doi_registry",
      url: "https://example.test/interface",
      isOpenAccess: true,
      relevanceScore: 0.9,
      relevanceLabel: "high",
      relevanceBreakdown: {
        scoringVersion: "science125-relevance-v1",
        titleCoverage: 0.9,
        abstractCoverage: 0.8,
        conceptCoverage: 1,
        phraseMatch: 0.7,
        evidenceCompleteness: 1,
        matchedConcepts: ["界面", "微观尺度", "测量方法"],
      },
      evidenceEligibility: {
        eligibleForGeneration: true,
        reasons: [],
        minimumRelevanceScore: 0.5,
        minimumRelevanceLabel: "medium",
        eligibilityVersion: "science125-evidence-eligibility-v1",
      },
      accessStatus: "open_access",
      needsFulltext: false,
      warning: "",
    }, {
      id: "result-openalex",
      title: "Nanoscale interface metrology",
      authors: "B. Researcher",
      journal: "Open Index",
      year: "2025",
      doi: "10.0000/openalex",
      abstract: "Independent full-text measurement evidence.",
      sourcePlatform: "OpenAlex",
      providerFamily: "scholarly_index",
      url: "https://example.test/openalex",
      isOpenAccess: true,
      relevanceScore: 0.8,
      relevanceLabel: "high",
      evidenceEligibility: {
        eligibleForGeneration: true,
        reasons: [],
        minimumRelevanceScore: 0.5,
        minimumRelevanceLabel: "medium",
        eligibilityVersion: "science125-evidence-eligibility-v1",
      },
      accessStatus: "open_full_text",
      needsFulltext: false,
      warning: "",
    }, {
      id: "result-europe-pmc",
      title: "Interfacial transport spectroscopy",
      authors: "C. Researcher",
      journal: "Europe PMC",
      year: "2024",
      doi: "10.0000/epmc",
      abstract: "A third reviewed full-text evidence record.",
      sourcePlatform: "Europe PMC",
      providerFamily: "biomedical_index",
      url: "https://example.test/epmc",
      isOpenAccess: true,
      relevanceScore: 0.7,
      relevanceLabel: "medium",
      evidenceEligibility: {
        eligibleForGeneration: true,
        reasons: [],
        minimumRelevanceScore: 0.5,
        minimumRelevanceLabel: "medium",
        eligibilityVersion: "science125-evidence-eligibility-v1",
      },
      accessStatus: "open_full_text",
      needsFulltext: false,
      warning: "",
    }, {
      id: "result-metadata",
      title: "Interface metadata lead",
      authors: "D. Researcher",
      journal: "Metadata Index",
      year: "2023",
      doi: "10.0000/metadata",
      abstract: "A relevant title without verified full text.",
      sourcePlatform: "Crossref",
      providerFamily: "doi_registry",
      url: "https://example.test/metadata",
      isOpenAccess: false,
      relevanceScore: 0.85,
      relevanceLabel: "high",
      evidenceEligibility: {
        eligibleForGeneration: false,
        reasons: ["ACCESS_NOT_FULL_TEXT"],
        minimumRelevanceScore: 0.5,
        minimumRelevanceLabel: "medium",
        eligibilityVersion: "science125-evidence-eligibility-v1",
      },
      accessStatus: "metadata",
      needsFulltext: true,
      warning: "",
    }, {
      id: "result-low",
      title: "Low relevance full text",
      authors: "E. Researcher",
      journal: "Open Archive",
      year: "2022",
      doi: "10.0000/low",
      abstract: "An unrelated administrative study.",
      sourcePlatform: "OpenAlex",
      providerFamily: "scholarly_index",
      url: "https://example.test/low",
      isOpenAccess: true,
      relevanceScore: 0.2,
      relevanceLabel: "very_low",
      evidenceEligibility: {
        eligibleForGeneration: false,
        reasons: ["RELEVANCE_BELOW_MEDIUM"],
        minimumRelevanceScore: 0.5,
        minimumRelevanceLabel: "medium",
        eligibilityVersion: "science125-evidence-eligibility-v1",
      },
      accessStatus: "open_full_text",
      needsFulltext: false,
      warning: "",
    }],
    platformStatus: {},
    warnings: [],
    evidenceStatus: "ready_for_review",
    evidenceReadiness: {
      eligibleFullTextCount: 3,
      minimumAcceptedEvidence: 3,
      providerFamilyCount: 3,
      minimumProviderFamilies: 2,
      providerFamilies: ["doi_registry", "scholarly_index", "biomedical_index"],
      minimumRelevanceLabel: "medium",
      ready: true,
    },
    refinementQueries: ["operando nanoscale interface spectroscopy"],
    query: {
      originalQueryText: "How can interface phenomena be measured on the microscopic level?",
      topicSummary: "interface; microscopic; nanoscale; transport kinetics",
      keywords: ["interface", "microscopic", "nanoscale", "operando spectroscopy", "transport kinetics", "calibration"],
      queries: [
        "interface interfacial phenomena microscopic nanoscale operando spectroscopy",
        "interface interfacial phenomena microscopic operando spectroscopy in situ microscopy",
      ],
    },
  },
  createdAt: "2026-07-15T00:00:00Z",
  updatedAt: "2026-07-15T00:00:01Z",
}

async function mockScience125Workspace(
  page: Page,
  storedReview: Record<string, unknown>,
  options: { withOwnedPdf?: boolean; withCompletedSearch?: boolean; onExcerptRequest?: (body: unknown) => void } = {},
) {
  await page.addInitScript(({ key, value }) => {
    window.sessionStorage.setItem(key, JSON.stringify(value))
  }, {
    key: "chalk:science125:review-selection:1:S125-006",
    value: storedReview,
  })
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 1, username: "science125-user", createdAt: "2026-07-15T00:00:00Z" } }),
  }))
  await page.route("**/api/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok" }),
  }))
  await page.route("**/api/science-125/questions", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ manifestVersion: "science125-v1", routingVersion: "science125-routing-v1", data: [question] }),
  }))
  await page.route(`**/api/science-125/questions/${question.id}`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: question.id,
      headline: question.question,
      headlineZh: question.questionZh,
      sourceContext: question.sourceContext,
      contextSha256: question.sourceContextSha256,
      pdfPage: question.pdfPage,
      bookletPage: question.bookletPage,
      extractionVersion: question.extractionVersion,
      availability: "available",
    }),
  }))
  await page.route(`**/api/science-125/questions/${question.id}/profile`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      questionId: question.id,
      routingVersion: "science125-routing-v1",
      localizationVersion: "science125-zh-CN-v1",
      questionZh: question.questionZh,
      searchIntentZh: "微观尺度界面现象的原位测量、界面光谱、显微成像与计算交叉验证",
      recommendedQuery: "interfacial chemistry microscopic interface phenomena measurement spectroscopy microscopy nanoscale dynamics",
      translationReviewStatus: "reviewed",
      benchmarkDomain: question.benchmarkDomain,
      primarySubdomain: question.primarySubdomain,
      crossDomainTags: question.crossDomainTags,
      methodProfile: question.methodProfile,
      promptProfile: question.promptProfile,
      retrievalProfile: question.retrievalProfile,
      classificationReviewStatus: "reviewed",
      ready: true,
      pilotEnabled: true,
      missingConfigurationCodes: [],
      providers: [
        { providerId: "crossref", displayName: "Crossref", baseUrl: "https://api.crossref.org/works", policySourceUrl: "https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/", authMode: "required", isRequired: true },
        { providerId: "openalex", displayName: "OpenAlex", baseUrl: "https://api.openalex.org/works", policySourceUrl: "https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication", authMode: "required", isRequired: false },
      ],
      providerReadiness: [
        { providerId: "crossref", ready: true, status: "ready", missingConfigurationCodes: [] },
        { providerId: "openalex", ready: true, status: "ready", missingConfigurationCodes: [] },
      ],
    }),
  }))
  await page.route(/\/api\/jobs\?.*/, (route) => {
    const url = new URL(route.request().url())
    const isCompletedLiteratureQuery = url.searchParams.get("type") === "literature_search"
      && url.searchParams.get("status") === "SUCCEEDED"
      && options.withCompletedSearch !== false
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: isCompletedLiteratureQuery ? [completedSearch] : [],
        pagination: { page: 1, pageSize: 100, totalItems: isCompletedLiteratureQuery ? 1 : 0, totalPages: 1 },
      }),
    })
  })
  await page.route("**/api/documents**", async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === "POST" && url.pathname.endsWith("/documents/17/page-excerpts")) {
      const body = route.request().postDataJSON()
      options.onExcerptRequest?.(body)
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          documentId: 17,
          title: "Owned interface paper",
          pages: [2, 4, 5],
          text: "Page 2 reports the measurement method. Pages 4-5 report controls and limitations.",
          hash: "page-excerpt-hash",
          provenance: {
            sourceType: "pdf",
            pdfSha256: "pdf-hash",
            textHash: "page-excerpt-hash",
            extractor: "pymupdf",
            truncated: false,
          },
        }),
      })
    }
    const documents = options.withOwnedPdf ? [{
      id: 17,
      title: "Owned interface paper",
      sourceType: "pdf",
      fileName: "interface.pdf",
      summary: null,
      createdAt: "2026-07-15T00:00:00Z",
    }] : []
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: documents, pagination: { page: 1, pageSize: 100, totalItems: documents.length, totalPages: documents.length ? 1 : 0 } }),
    })
  })
  await page.route("**/api/multimodal/runs?pageSize=100", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
  }))
}

test("toggles the selected Science 125 question off when clicked again", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: "science125-search-current",
    selectedIds: [],
    confirmed: false,
  })

  await page.goto("/research/general/new")
  const questionButton = page.getByTestId("science125-question-S125-006")
  await questionButton.click()
  await expect(questionButton).toHaveAttribute("aria-pressed", "true")
  await expect(page).toHaveURL(/question=S125-006$/)

  await questionButton.click()
  await expect(page).toHaveURL(/\/research\/general\/new$/)
  await expect(questionButton).toHaveAttribute("aria-pressed", "false")
  await expect(page.getByTestId("science125-empty-selection")).toBeVisible()
  await expect(page.getByTestId("begin-science125-presearch")).toHaveCount(0)
})

test("shows the authoritative booklet context separately from the headline", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: "science125-search-current",
    selectedIds: [],
    confirmed: false,
  })

  await page.goto("/research/general/new?question=S125-006")
  await expect(page.getByTestId("science125-question-zh")).toContainText(question.questionZh)
  await expect(page.getByTestId("science125-question-en")).toContainText(question.question)
  await expect(page.getByTestId("science125-booklet-context")).toContainText(question.sourceContext)
})

test("prefills an editable literature query from the booklet context", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: completedSearch.id,
    selectedIds: [],
    confirmed: false,
  })

  await page.goto("/research/general/new?question=S125-006&stage=presearch")
  const query = page.getByTestId("science125-literature-query")
  await expect(page.getByTestId("science125-search-intent-zh")).toContainText("界面光谱")
  await expect(query).toBeEnabled()
  await expect(query).toHaveValue(/microscopic/i)
  await expect(query).toHaveValue(/interface/i)
  await expect(query).toHaveValue(/interfacial/i)
  await expect(query).toHaveValue(/chemistry/i)
  const relevance = page.getByTestId("science125-relevance-result-current")
  await expect(relevance).toContainText("90%")
  await expect(relevance).toContainText("高")
  await expect(relevance).toHaveAttribute("title", /核心概念/)
  await query.fill("custom interface spectroscopy query")
  await expect(query).toHaveValue("custom interface spectroscopy query")
})

test("groups questions by the reviewed domain route and shows automatic provider routing", async ({ page }, testInfo) => {
  await mockScience125Workspace(page, {
    jobId: completedSearch.id,
    selectedIds: [],
    confirmed: false,
  })

  await page.goto("/research/general/new")
  if (testInfo.project.name === "mobile") {
    await page.getByRole("combobox", { name: "Science 125 领域" }).selectOption("Chemistry")
  } else {
    const domainNavigation = page.getByRole("navigation", { name: "Science 125 一级领域" })
    const chemistryDomain = domainNavigation.getByRole("button", { name: /化学.*Chemistry/ })
    await expect(chemistryDomain).toContainText("化学")
    await expect(chemistryDomain).toContainText("Chemistry")
    await chemistryDomain.click()
  }
  await expect(page.getByTestId("science125-question-S125-006")).toContainText("chem.interface")
  await page.getByTestId("science125-question-S125-006").click()
  await page.getByTestId("begin-science125-presearch").click()
  await expect(page.getByTestId("science125-provider-routing")).toContainText("Crossref")
})

test("adds selected pages from an owned PDF to the reviewed hypothesis input", async ({ page }) => {
  let excerptRequest: unknown = null
  await mockScience125Workspace(page, {
    jobId: completedSearch.id,
    selectedIds: [],
    confirmed: false,
  }, {
    withOwnedPdf: true,
    onExcerptRequest: (body) => { excerptRequest = body },
  })

  await page.goto("/research/general/new?question=S125-006&stage=presearch")
  await page.getByTestId("science125-document-select").selectOption("17")
  await page.getByTestId("science125-page-input").fill("2, 4-5")
  await page.getByTestId("science125-add-page-excerpt").click()

  await expect(page.getByTestId("science125-page-excerpt-17")).toContainText("2, 4-5")
  expect(excerptRequest).toEqual({ pages: [2, 4, 5], maxChars: 12000 })
  await page.getByLabel("选择检索结果 Interface measurement study").check()
  await page.getByLabel("选择检索结果 Nanoscale interface metrology").check()
  await page.getByTestId("use-literature-for-hypothesis").click()

  await expect(page).toHaveURL(/question=S125-006&stage=hypothesis/)
  await expect(page.getByTestId("science125-input-source")).toHaveAttribute("data-source-type", "booklet-context")
  await expect(page.getByTestId("science125-reviewed-evidence")).toContainText("Owned interface paper")
  await expect(page.getByTestId("science125-reviewed-evidence")).toContainText("Page 2 reports the measurement method")
  await expect(page.getByTestId("start-hypothesis")).toBeVisible()
})

test("counts only medium-or-higher full text and explains every rejected lead", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: completedSearch.id,
    selectedIds: [],
    confirmed: false,
  })

  await page.goto("/research/general/new?question=S125-006&stage=presearch")
  await page.getByLabel("选择检索结果 Interface metadata lead").check()
  await page.getByLabel("选择检索结果 Low relevance full text").check()

  await expect(page.getByTestId("science125-eligible-fulltext-count")).toHaveText("合格全文 0/3")
  await expect(page.getByTestId("science125-provider-family-count")).toHaveText("来源家族 0/2")
  await expect(page.getByTestId("science125-evidence-ineligible-result-metadata")).toContainText("仅元数据线索")
  await expect(page.getByTestId("science125-evidence-ineligible-result-low")).toContainText("相关度不足")
  await expect(page.getByTestId("use-literature-for-hypothesis")).toBeDisabled()
})

test("shows the server-extracted topic, keywords, and short provider queries", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: completedSearch.id,
    selectedIds: [],
    confirmed: false,
  })

  await page.goto("/research/general/new?question=S125-006&stage=presearch")

  await expect(page.getByTestId("science125-topic-summary")).toContainText("interface")
  await expect(page.getByTestId("science125-query-plan")).toContainText("operando spectroscopy")
  await expect(page.getByTestId("science125-executed-queries").locator("li")).toHaveCount(2)
  await expect(page.getByTestId("science125-executed-queries")).not.toContainText(
    "How can interface phenomena be measured on the microscopic level?",
  )
})

test("does not let reviewed PDF pages alone bypass the evidence gate", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: "science125-search-missing",
    selectedIds: [],
    confirmed: false,
  }, {
    withOwnedPdf: true,
    withCompletedSearch: false,
  })

  await page.goto("/research/general/new?question=S125-006&stage=presearch")
  await page.getByTestId("science125-document-select").selectOption("17")
  await page.getByTestId("science125-page-input").fill("999999999999999999999999999999")
  await page.getByTestId("science125-add-page-excerpt").click()
  await expect(page.getByText("页码必须是有限的整数。")).toBeVisible()

  await page.getByTestId("science125-page-input").fill("2, 4-5")
  await page.getByTestId("science125-add-page-excerpt").click()
  await expect(page.getByTestId("use-literature-for-hypothesis")).toBeDisabled()
  await expect(page.getByTestId("science125-eligible-fulltext-count")).toHaveText("合格全文 1/3")
  await expect(page.getByTestId("science125-provider-family-count")).toHaveText("来源家族 1/2")
  await expect(page.getByText("尚未达到生成门槛。")).toBeVisible()
})

test("does not apply an old Science 125 review selection to a newer search job", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: "science125-search-old",
    selectedIds: ["result-current"],
    confirmed: true,
  })

  await page.goto(`/research/general/new?question=${question.id}&stage=hypothesis`)

  await expect(page).toHaveURL(new RegExp(`question=${question.id}&stage=presearch`))
  await expect(page.getByTestId("start-literature-presearch")).toBeVisible()
  await expect(page.getByTestId("hypothesis-generation-blocked")).toHaveCount(0)
})

test("keeps a confirmed same-job review in the prompt-configuration boundary", async ({ page }) => {
  await mockScience125Workspace(page, {
    jobId: completedSearch.id,
    selectedIds: ["result-current", "result-openalex", "result-europe-pmc"],
    confirmed: true,
  })

  await page.goto(`/research/general/new?question=${question.id}&stage=hypothesis`)

  await expect(page.getByTestId("hypothesis-generation-blocked")).toHaveCount(0)
  await expect(page.getByTestId("start-hypothesis")).toBeVisible()
})

test("downloads Science 125 report exports from the backend API", async ({ page }) => {
  const reportId = "11111111-1111-1111-1111-111111111111"
  const batchId = "22222222-2222-2222-2222-222222222222"
  const exportId = "33333333-3333-3333-3333-333333333333"
  const timestamp = "2026-08-11T00:00:00Z"

  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 1, username: "science125-user", createdAt: timestamp } }),
  }))
  await page.route("**/api/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok" }),
  }))
  await page.route("**/api/science-125/batches", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([]),
  }))
  await page.route(/\/api\/science-125\/reports(?:\?.*)?$/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{
      reportId,
      batchId,
      questionId: question.id,
      question: question.question,
      questionZh: question.questionZh,
      benchmarkDomain: question.benchmarkDomain,
      primarySubdomain: question.primarySubdomain,
      status: "SUCCEEDED",
      attemptNumber: 1,
      selectedHypothesisId: "H1",
      selectedHypothesisConfidence: 0.8,
      selectedHypothesisReason: "Highest confidence candidate.",
      evidenceStatus: "sufficient",
      selectedEvidenceCount: 3,
      providerFamilies: ["doi_registry", "scholarly_index"],
      model: "qwen3.8-max",
      requestId: "request-1",
      totalTokens: 100,
      latencyMs: 1000,
      estimatedCostCny: 0.01,
      createdAt: timestamp,
      updatedAt: timestamp,
      sourceType: "interactive_job",
      sourceJobId: "job-1",
    }]),
  }))
  await page.route(`**/api/science-125/reports/${reportId}/exports`, (route) => route.fulfill({
    status: 201,
    contentType: "application/json",
    body: JSON.stringify({
      exportId,
      batchId,
      reportId,
      format: "json",
      fileName: `${question.id}.json`,
      mimeType: "application/json",
      sizeBytes: 100,
      createdAt: timestamp,
    }),
  }))
  await page.route(`**/api/science-125/exports/${exportId}`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ questionId: question.id }),
  }))

  await page.goto("/research/general/reports")
  const downloadRequest = page.waitForRequest(`**/api/science-125/exports/${exportId}`)
  await page.getByRole("button", { name: "JSON", exact: true }).click()

  expect(new URL((await downloadRequest).url()).origin).toBe("http://127.0.0.1:8000")
})

test("creates the fixed 10-question preproduction batch and deletes an idle batch", async ({ page }) => {
  const batchId = "44444444-4444-4444-4444-444444444444"
  const timestamp = "2026-08-11T00:00:00Z"
  const expectedQuestionIds = [
    "S125-001", "S125-004", "S125-006", "S125-013", "S125-024",
    "S125-043", "S125-054", "S125-069", "S125-107", "S125-118",
  ]
  let createdQuestionIds: string[] = []
  let deletedBatchId = ""
  const batch = {
    batchId,
    manifestVersion: "science125-v1",
    manifestSha256: "a".repeat(64),
    routingVersion: "science125-routing-v1",
    routingSha256: "b".repeat(64),
    promptVersion: "science125-prompts-v2",
    promptRegistrySha256: "c".repeat(64),
    model: "qwen3.8-max",
    status: "DRAFT",
    totalCount: 10,
    succeededCount: 0,
    failedCount: 0,
    blockedEvidenceCount: 0,
    totalTokens: 0,
    estimatedCostCny: 0,
    startedAt: null,
    completedAt: null,
    createdAt: timestamp,
    updatedAt: timestamp,
  }

  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 1, username: "science125-user", createdAt: timestamp } }),
  }))
  await page.route("**/api/health", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok" }),
  }))
  await page.route("**/api/science-125/batches", async (route) => {
    if (route.request().method() === "POST") {
      createdQuestionIds = (await route.request().postDataJSON()).questionIds
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ ...batch, questionIds: expectedQuestionIds, reports: [] }) })
      return
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([batch]) })
  })
  await page.route(/\/api\/science-125\/reports(?:\?.*)?$/, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([]),
  }))
  await page.route(`**/api/science-125/batches/${batchId}`, async (route) => {
    if (route.request().method() === "DELETE") {
      deletedBatchId = batchId
      await route.fulfill({ status: 204 })
      return
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...batch, questionIds: expectedQuestionIds, reports: [] }) })
  })

  await page.goto("/research/general/reports")
  await page.getByRole("button", { name: "创建 10 题预生产批次" }).click()
  expect(createdQuestionIds).toEqual(expectedQuestionIds)

  page.once("dialog", (dialog) => dialog.accept())
  await page.getByRole("button", { name: "删除批次" }).click()
  await expect.poll(() => deletedBatchId).toBe(batchId)
})

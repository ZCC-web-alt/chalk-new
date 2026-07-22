import { expect, test, type Page } from "@playwright/test"

const question = {
  id: "S125-006",
  question: "How can we measure interface phenomena on the microscopic level?",
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
      accessStatus: "open_full_text",
      needsFulltext: false,
      warning: "",
    }],
    platformStatus: {},
    warnings: [],
    evidenceStatus: "ready_for_review",
    query: {},
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
  await expect(query).toBeEnabled()
  await expect(query).toHaveValue(/microscopic/i)
  await expect(query).toHaveValue(/interface/i)
  await expect(query).toHaveValue(/interfacial/i)
  await expect(query).toHaveValue(/chemistry/i)
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
    await domainNavigation.getByRole("button", { name: /^Chemistry/ }).click()
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
  await expect(page.getByText("全文证据或来源家族不足，暂不能生成。")).toBeVisible()
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

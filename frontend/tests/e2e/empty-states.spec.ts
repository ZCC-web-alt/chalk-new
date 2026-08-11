import { expect, test, type Page } from "@playwright/test"


async function mockAuthenticatedEmptyWorkspace(page: Page) {
  await page.route("**/api/auth/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 1, username: "test-user", createdAt: "2026-01-01T00:00:00Z" } }),
  }))
  await page.route("**/api/documents**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data: [], pagination: { page: 1, pageSize: 100, totalItems: 0, totalPages: 0 } }),
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


test("hypothesis generation starts with an honest empty state", async ({ page }) => {
  await mockAuthenticatedEmptyWorkspace(page)
  await page.goto("/")

  await page.locator('[data-nav-key="hypothesis"]').click()
  await expect(page.getByTestId("hypothesis-empty")).toBeVisible()
  await expect(page.getByTestId("start-hypothesis")).toBeDisabled()
})

test("API settings lets a user save external provider keys without returning tokens", async ({ page }) => {
  await mockAuthenticatedEmptyWorkspace(page)
  let savedPayload: unknown = null
  const savedProviders = new Set<string>()
  await page.route("**/api/settings/api-keys", async (route) => {
    if (route.request().method() === "PUT") {
      savedPayload = route.request().postDataJSON()
      savedProviders.add((savedPayload as { provider: string }).provider)
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured: {
          dashscope: true,
          semantic_scholar: true,
          ncbi: savedProviders.has("ncbi"),
          ncbi_tool_email: savedProviders.has("ncbi_tool_email"),
          crossref_mailto: true,
          nasa_ads: savedProviders.has("nasa_ads"),
          materials_project: savedProviders.has("materials_project"),
        },
        effective: { ncbi: savedProviders.has("ncbi") && savedProviders.has("ncbi_tool_email") },
      }),
    })
  })

  await page.goto("/")
  await page.locator('[data-nav-key="api"]').click()

  const ncbiProvider = page.getByTestId("api-provider-ncbi")
  await ncbiProvider.getByLabel("NCBI API Key").fill("ncbi-secret-key")
  await ncbiProvider.getByRole("button", { name: "保存" }).click()
  expect(savedPayload).toEqual({ provider: "ncbi", apiKey: "ncbi-secret-key" })
  await expect(ncbiProvider).toContainText("待补全")

  const ncbiEmailProvider = page.getByTestId("api-provider-ncbi_tool_email")
  const ncbiEmailInput = ncbiEmailProvider.getByLabel("NCBI Tool Email")
  await expect(ncbiEmailInput).toHaveAttribute("type", "email")
  await ncbiEmailInput.fill("team@example.org")
  await ncbiEmailProvider.getByRole("button", { name: "保存" }).click()
  expect(savedPayload).toEqual({ provider: "ncbi_tool_email", apiKey: "team@example.org" })
  await expect(ncbiProvider).toContainText("生效中")
  await expect(ncbiEmailProvider).toContainText("生效中")

  const nasaProvider = page.getByTestId("api-provider-nasa_ads")
  const nasaInput = nasaProvider.getByLabel("NASA ADS API Token")
  await expect(nasaInput).toBeVisible()
  await expect(nasaInput).toHaveAttribute("type", "password")
  await nasaInput.fill("ads-secret-token")
  await nasaProvider.getByRole("button", { name: "保存" }).click()

  expect(savedPayload).toEqual({ provider: "nasa_ads", apiKey: "ads-secret-token" })
  await expect(nasaProvider).toContainText("已配置")
  await expect(nasaInput).toHaveValue("")

  const materialsProvider = page.getByTestId("api-provider-materials_project")
  const materialsInput = materialsProvider.getByLabel("Materials Project API Key")
  await expect(materialsInput).toBeVisible()
  await expect(materialsInput).toHaveAttribute("type", "password")
  await materialsInput.fill("mp-secret-key")
  await materialsProvider.getByRole("button", { name: "保存" }).click()

  expect(savedPayload).toEqual({ provider: "materials_project", apiKey: "mp-secret-key" })
  await expect(materialsProvider).toContainText("已配置")
  await expect(materialsInput).toHaveValue("")
})

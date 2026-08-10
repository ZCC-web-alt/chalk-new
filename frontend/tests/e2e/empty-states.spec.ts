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
  await page.route("**/api/settings/api-keys", async (route) => {
    if (route.request().method() === "PUT") {
      savedPayload = route.request().postDataJSON()
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured: {
          dashscope: true,
          semantic_scholar: true,
          ncbi: true,
          crossref_mailto: true,
          nasa_ads: savedPayload !== null,
          materials_project: savedPayload !== null,
        },
      }),
    })
  })

  await page.goto("/")
  await page.locator('[data-nav-key="api"]').click()

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

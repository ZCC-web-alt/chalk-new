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

test("API settings shows NASA ADS as a server-managed credential", async ({ page }) => {
  await mockAuthenticatedEmptyWorkspace(page)
  await page.route("**/api/settings/api-keys", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      configured: {
        dashscope: true,
        semantic_scholar: true,
        ncbi: true,
        crossref_mailto: true,
        nasa_ads: true,
      },
    }),
  }))

  await page.goto("/")
  await page.locator('[data-nav-key="api"]').click()

  const nasaAds = page.getByTestId("api-provider-nasa_ads")
  await expect(nasaAds).toContainText("NASA ADS API Token")
  await expect(nasaAds).toContainText("已配置")
  await expect(nasaAds).toContainText("服务器环境变量管理")
  await expect(page.getByLabel("NASA ADS API Token")).toHaveCount(0)
})

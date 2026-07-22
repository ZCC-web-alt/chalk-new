import { expect, test } from "@playwright/test"

function exactPath(path: string) {
  return new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`)
}

test("registers, restores the session, loads real resources, logs in, and logs out", async ({ page }) => {
  const username = `fullstack-${Date.now()}`
  const password = "chalk-fullstack-password"

  await page.goto("/")
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.locator('form button[type="button"]').last().click()

  const documentsResponse = page.waitForResponse((response) => (
    response.request().method() === "GET" && response.url().includes("/api/documents")
  ))
  await page.locator('form button[type="submit"]').click()

  await expect(page.locator('[data-nav-key="literature"]')).toBeVisible()
  const documents = await documentsResponse
  expect(documents.ok()).toBeTruthy()
  await expect(documents.json()).resolves.toMatchObject({ data: [] })

  await page.reload()
  await expect(page.locator('[data-nav-key="literature"]')).toBeVisible()

  await page.locator("header button").click()
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()

  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()
  await expect(page.locator('[data-nav-key="literature"]')).toBeVisible()

  await page.locator("header button").click()
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
})

test("returns to the complete research deep link when login overlaps the auth redirect", async ({ page }) => {
  const username = `deep-link-${Date.now()}`
  const password = "chalk-deep-link-password"
  const deepLink = "/research/general/new?sourceDocumentId=doc-alpha&sourceDocumentId=doc-beta&focus=interface%20stability"

  await page.goto("/")
  await page.locator('form button[type="button"]').last().click()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="new-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()
  await expect(page.locator('[data-nav-key="literature"]')).toBeVisible()

  await page.locator("header button").click()
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()

  await page.goto(deepLink)
  await expect(page).toHaveURL(/\/\?next=/)
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
  await page.evaluate(() => window.history.pushState(null, "", window.location.href))
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()
  await expect(page).toHaveURL(exactPath(deepLink))

  await page.goBack()
  await expect(page).toHaveURL(exactPath(deepLink))
})

test("rejects a research return path that normalizes outside the research namespace", async ({ page }) => {
  const username = `unsafe-return-${Date.now()}`
  const password = "chalk-unsafe-return-password"
  const unsafeReturnPath = "/research/../admin?from=research"

  await page.goto("/")
  await page.locator('form button[type="button"]').last().click()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="new-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()
  await expect(page.locator('[data-nav-key="literature"]')).toBeVisible()

  await page.goto(`/?next=${encodeURIComponent(unsafeReturnPath)}`)
  await expect(page.locator('[data-nav-key="literature"]')).toBeVisible()
  await expect(page).toHaveURL(/\/\?next=/)
})

test("opens the Science 125 workspace, selects an authoritative question, and enters literature presearch", async ({ page }) => {
  const username = `science125-${Date.now()}`
  const password = "chalk-science125-password"

  await page.goto("/")
  await page.locator('form button[type="button"]').last().click()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="new-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()
  await expect(page.locator('[data-nav-key="research-general"]')).toBeVisible()
  await expect(page.locator('[data-nav-key="research-chemistry"]')).toHaveCount(0)
  await expect(page.locator('[data-nav-key="research-projects"]')).toHaveCount(0)

  const catalogResponse = page.waitForResponse((response) => (
    response.request().method() === "GET" && response.url().includes("/api/science-125/questions")
  ))
  await page.locator('[data-nav-key="research-general"]').click()
  await expect(page).toHaveURL(/\/research\/general\/new/)
  expect((await catalogResponse).ok()).toBeTruthy()
  await expect(page.getByRole("button", { name: "返回主页面" })).toBeVisible()
  await page.getByTestId("science125-question-S125-006").click()
  await expect(page).toHaveURL(/question=S125-006$/)
  await expect(page.getByTestId("science125-booklet-context")).toContainText("How can we measure interface phenomena on the microscopic level?")
  await expect(page.getByRole("button", { name: "开始文献预搜索" })).toBeVisible()
  await page.reload()
  await expect(page).toHaveURL(/question=S125-006$/)
  await expect(page.getByRole("button", { name: "开始文献预搜索" })).toBeVisible()
  await page.getByTestId("begin-science125-presearch").click()
  await expect(page.getByText("文献预搜索").last()).toBeVisible()
  await expect(page).toHaveURL(/question=S125-006&stage=presearch/)
  const literatureQuery = page.getByTestId("science125-literature-query")
  await expect(literatureQuery).toHaveValue(/interface/i)
  await expect(literatureQuery).toHaveValue(/microscopic/i)
  await expect(literatureQuery).toBeEnabled()
  await page.getByRole("button", { name: "返回主页面" }).click()
  await expect(page).toHaveURL(/\/$/)
})

test("does not allow a Science 125 URL to skip literature review", async ({ page }) => {
  const username = `science125-stage-${Date.now()}`
  const password = "chalk-science125-password"

  await page.goto("/")
  await page.locator('form button[type="button"]').last().click()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="new-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()

  await page.goto("/research/general/new?question=S125-006&stage=hypothesis")
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.locator('form button[type="submit"]').click()
  await expect(page).toHaveURL(/question=S125-006&stage=presearch/)
  await expect(page.getByTestId("start-literature-presearch")).toBeVisible()
  await expect(page.getByTestId("hypothesis-generation-blocked")).toHaveCount(0)
})

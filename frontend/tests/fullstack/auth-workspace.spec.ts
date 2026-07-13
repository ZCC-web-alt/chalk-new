import { expect, test } from "@playwright/test"

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

import { expect, test } from "@playwright/test";

const annualId = "11111111-1111-4111-8111-111111111111";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("northstar-demo-introduction-v1", "seen"));
});

test("mobile navigation stays single-line and keeps all destinations", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto("/");
  const links = page.getByRole("navigation", { name: "Portal navigation" }).getByRole("link");
  await expect(links).toHaveCount(7);
  for (const link of await links.all()) {
    await expect(link).toBeVisible();
    expect(await link.evaluate((element) => getComputedStyle(element).whiteSpace)).toBe("nowrap");
  }
  expect(errors).toEqual([]);
});

test("mobile review shows the authoritative draft before the explainer", async ({ page }) => {
  await page.goto(`/leave/review/${annualId}`);
  const main = await page.locator(".review-main").boundingBox();
  const aside = await page.locator(".review-aside").boundingBox();
  expect(main).not.toBeNull();
  expect(aside).not.toBeNull();
  expect(main?.y ?? 0).toBeLessThan(aside?.y ?? 0);
  await expect(page.getByText("This is a draft — nothing has been submitted yet.")).toBeVisible();
});

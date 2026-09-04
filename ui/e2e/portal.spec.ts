import { expect, test } from "@playwright/test";

const annualId = "11111111-1111-4111-8111-111111111111";
const itId = "22222222-2222-4222-8222-222222222222";
const succeededId = "33333333-3333-4333-8333-333333333333";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("northstar-demo-introduction-v1", "seen"));
});

test("desktop navigation and trusted identity stay employee-scoped", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Portal navigation" });
  await expect(nav.getByRole("link")).toHaveCount(7);
  await expect(page.getByRole("heading", { name: "Welcome back, Alex." })).toBeVisible();
  await page.getByLabel("Current demo identity: Alex Morgan").click();
  await page.getByRole("button", { name: /Sam Lee/ }).click();
  await expect(page.getByRole("heading", { name: "Welcome back, Sam." })).toBeVisible();
  await expect(page.getByText("Ready for your review")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("assistant renders bounded markdown and citations preserve transcript state", async ({ page, context }) => {
  await page.goto("/assistant");
  await page.getByRole("button", { name: /Carry over leave/ }).click();
  const answer = page.locator(".assistant-markdown");
  await expect(answer.getByText("Yes.")).toHaveCount(1);
  await expect(answer.getByRole("listitem")).toHaveCount(2);
  await expect(answer.locator("script")).toHaveCount(0);
  const citation = page.getByRole("link", { name: /Annual Leave Policy/ });
  await expect(citation).toHaveAttribute("target", "_blank");
  const newPagePromise = context.waitForEvent("page");
  await citation.click();
  const policyPage = await newPagePromise;
  await expect(policyPage.getByRole("heading", { name: "Annual Leave Policy" })).toBeVisible();
  await expect(page.locator(".assistant-markdown")).toContainText("Approved policy supports carry-over");
});

test("unknown portal resources use product recovery instead of framework 404", async ({ page }) => {
  await page.goto("/requests/not-a-valid-action");
  await expect(page.getByRole("heading", { name: "We couldn’t find that portal item." })).toBeVisible();
  await expect(page.getByRole("link", { name: "View my requests" })).toBeVisible();
  await expect(page.locator("#main-content").getByRole("link", { name: "Policy library" })).toBeVisible();
});

test("annual leave review preserves exact consent copy and state-aware outcome", async ({ page }) => {
  await page.goto(`/leave/review/${annualId}`);
  await expect(page.getByText("This is a draft — nothing has been submitted yet.")).toBeVisible();
  await expect(page.getByText("Personal day")).toBeVisible();
  await page.getByRole("button", { name: "Begin authorization" }).click();
  await page.getByRole("checkbox").check();
  await expect(page.getByText("By submitting, you authorize this exact request.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Submit leave request" })).toBeEnabled();

  await page.goto(`/leave/review/${succeededId}`);
  await expect(page.getByText("This leave request was submitted successfully.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your leave is recorded." })).toBeVisible();
});

test("IT review saves an immutable editable revision before authorization", async ({ page }) => {
  await page.goto(`/it/review/${itId}`);
  await expect(page.getByText("This is a draft — nothing has been submitted yet.")).toBeVisible();
  await page.getByLabel("Summary").fill("Laptop battery will not charge");
  await page.getByRole("button", { name: "Save as new revision" }).click();
  await expect(page.getByText("Persisted · Revision 2")).toBeVisible();
  await expect(page.getByLabel("Summary")).toHaveValue("Laptop battery will not charge");
});

import { E2E_PASSWORD, E2E_USER, expect, login, test } from "./helpers";

test.describe("programs catalog", () => {
  test("a guest sees the preview and signs in in place", async ({ page }) => {
    await page.context().clearCookies();
    await page.goto("/programs");

    const preview = page.locator(".auth-gate__preview");
    await expect(preview).toHaveAttribute("aria-hidden", "true");
    await expect(page.locator(".programs-page__header h1")).toHaveText(
      "Мои программы",
    );
    await expect(page.locator(".program-card")).toHaveCount(0);
    await expect(page.locator(".auth-gate__overlay")).toBeVisible();

    await page
      .locator(".auth-gate__overlay")
      .getByRole("button", { name: "Войти" })
      .click();
    const dialog = page.getByRole("dialog", { name: "Вход в аккаунт" });
    await dialog.locator("#username").fill(E2E_USER);
    await dialog.locator("#password").fill(E2E_PASSWORD);
    await dialog.getByRole("button", { name: "Войти" }).click();

    await expect(dialog).toBeHidden();
    await expect(page.locator(".auth-gate__overlay")).toHaveCount(0);
    await expect(preview).not.toHaveAttribute("aria-hidden", "true");
    await expect(
      page.getByRole("heading", { name: "Мои программы" }),
    ).toBeVisible();
    await expect(page.locator(".program-card")).toHaveCount(6);
  });

  test("announces pagination and moves focus to the results", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/programs");

    const summary = page.locator(".pagination__info");
    await expect(summary).toHaveAttribute("aria-live", "polite");
    await expect(summary).toHaveAttribute("aria-atomic", "true");

    await page.getByRole("button", { name: "Вперёд" }).click();

    await expect(page.locator(".programs-page__results")).toBeFocused();
    await expect(page.locator(".program-card")).toHaveCount(2);
    await expect(summary).toContainText("Показано 7–8 из 8 программ");
  });

  test("opens the detail panel full-screen on a narrow viewport", async ({
    page,
  }) => {
    await login(page);
    await page.setViewportSize({ width: 900, height: 800 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/programs");

    await page.locator(".program-card__open").first().click();

    const panel = page.locator(".program-panel");
    await expect(panel).toBeVisible();

    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(880);
    expect(box!.x).toBeLessThanOrEqual(10);
  });
});

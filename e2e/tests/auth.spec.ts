import { E2E_PASSWORD, E2E_USER, expect, login, openSignInDialog, test } from "./helpers";

test.describe("authentication", () => {
  test("rejects a wrong password with a visible message", async ({ page }) => {
    await page.goto("/");
    const dialog = await openSignInDialog(page);
    await dialog.locator("#username").fill(E2E_USER);
    await dialog.locator("#password").fill("definitely-wrong");
    await dialog.getByRole("button", { name: "Войти" }).click();

    // A failed login raises a toast, not an inline field error.
    await expect(
      page.getByText("Перепроверьте правильность введённого логина и пароля"),
    ).toBeVisible();
    await expect(dialog).toBeVisible();

    // Escape is a cancel request: the dialog must close.
    await dialog.press("Escape");
    await expect(dialog).toBeHidden();
  });

  test("submits the sign-in form by pressing Enter", async ({ page }) => {
    await page.goto("/");
    const dialog = await openSignInDialog(page);
    await dialog.locator("#username").fill(E2E_USER);
    await dialog.locator("#password").fill(E2E_PASSWORD);

    // Native implicit submission: Enter in a field submits the form.
    await dialog.locator("#password").press("Enter");

    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/home$/);
  });

  test("signs in through the dialog and lands on the app", async ({ page }) => {
    await login(page);

    await expect(page).toHaveURL(/\/home$/);
    await expect(page.locator(".profile__name")).toHaveText(E2E_USER);
  });

  test("a guest stays on a protected route and sees the sign-in prompt", async ({
    page,
  }) => {
    await page.context().clearCookies();
    await page.goto("/chats");

    await expect(page).toHaveURL(/\/chats$/);
    await expect(page.getByRole("heading", { name: "Требуется вход" })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("a guest signs in from the gate prompt and gets the page they asked for", async ({
    page,
  }) => {
    await page.context().clearCookies();
    await page.goto("/create");

    const prompt = page.locator(".login-prompt");
    await expect(prompt.getByRole("heading", { name: "Требуется вход" })).toBeVisible();
    await prompt.getByRole("button", { name: "Войти" }).click();

    const dialog = page.getByRole("dialog", { name: "Вход в аккаунт" });
    await dialog.locator("#username").fill(E2E_USER);
    await dialog.locator("#password").fill(E2E_PASSWORD);
    await dialog.getByRole("button", { name: "Войти" }).click();

    await expect(page).toHaveURL(/\/create$/);
    await expect(page.getByRole("heading", { name: "Новый чат" })).toBeVisible();
  });

  test("guests read the public pages without a prompt", async ({ page }) => {
    await page.context().clearCookies();

    await page.goto("/home");
    await expect(
      page.getByRole("heading", { name: "Готовы к созданию новой программы?" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Требуется вход" }),
    ).toHaveCount(0);

    await page.goto("/help");
    await expect(page.getByRole("heading", { name: "Помощь" })).toBeVisible();
  });

  test("signs out in place without leaving the page and protects it again", async ({
    page,
  }) => {
    await login(page);

    await page.goto("/profile");
    await page.getByRole("button", { name: "Открыть меню профиля" }).click();
    await page.getByRole("button", { name: "Выход" }).click();

    // Logout no longer forces a redirect: the gate takes over on the same url.
    await expect(page).toHaveURL(/\/profile$/);
    await expect(page.getByText("Вы вышли из аккаунта")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Требуется вход" })).toBeVisible();

    // The cookie is gone: a protected route stays put and asks for a login.
    await page.goto("/create");
    await expect(page).toHaveURL(/\/create$/);
    await expect(page.getByRole("heading", { name: "Требуется вход" })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

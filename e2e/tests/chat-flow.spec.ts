import { expect, login, test } from "./helpers";

/**
 * The full chat journey, against the real Compose stack with the offline
 * provider. This exercises the router, the TaskIQ worker and the upload to the
 * provider; only the LLM itself is mocked.
 */
test.describe("chat conversation", () => {
  test("creates a chat, gets an answer and shows it in the transcript", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/create");

    await expect(page.getByRole("heading", { name: "Новый чат" })).toBeVisible();
    await page
      .locator("#description")
      .fill("Собери основные тезисы из приложенного документа");

    await page.getByRole("button", { name: /Создать чат/ }).click();

    // The create page hands the draft over and routes into the chat.
    await expect(page).toHaveURL(/\/chats\/.+/);
    await expect(
      page.getByText("Собери основные тезисы из приложенного документа"),
    ).toBeVisible();

    // The worker pipeline produces an assistant reply.
    await expect(
      page.locator(".message__author", { hasText: "Агент" }).first(),
    ).toBeVisible({ timeout: 45_000 });

    // The side panel lists the session's files. Nothing generates one any more,
    // so this is where the empty state has to be.
    await page.getByRole("button", { name: "Панель файлов" }).click();
    await expect(page.locator(".files-aside")).toBeVisible();
    await expect(page.getByText("Нет файлов в загрузке")).toBeVisible();
  });

  test("answers a follow-up message in the same session", async ({ page }) => {
    await login(page);
    await page.goto("/create");
    await page.locator("#description").fill("Что здесь нужно уточнить?");
    await page.getByRole("button", { name: /Создать чат/ }).click();
    await expect(page).toHaveURL(/\/chats\/.+/);

    await expect(page.getByText("Что здесь нужно уточнить?")).toBeVisible();
    await expect(
      page.locator(".message__author", { hasText: "Агент" }).first(),
    ).toBeVisible({ timeout: 45_000 });

    // app-input renders both a hidden input and the textarea, so target the
    // textarea directly rather than the placeholder.
    await page
      .locator('textarea[placeholder="Введите сообщение..."]')
      .fill("Сделай это короче");
    await page.getByRole("button", { name: "Отправить" }).click();

    // A second agent turn appears alongside the first.
    await expect(page.locator(".message__author", { hasText: "Агент" })).toHaveCount(2, {
      timeout: 45_000,
    });
  });
});

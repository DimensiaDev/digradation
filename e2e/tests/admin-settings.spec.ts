import type { Locator, Page } from "@playwright/test";

import { expect, login, test } from "./helpers";

/**
 * The instance settings page (`/admin/settings`), reached with the superuser
 * created by the e2e setup script.
 *
 * The editor talks to `PATCH /api/v1/admin/settings` with a partial body, so the
 * test saves through the real UI and then reloads to prove the value persisted on
 * the server. The test restores the previous value at the end: the settings row is
 * instance-wide and the suite runs as one user, so a leftover value would leak
 * into every later test.
 *
 * Editing always goes through real keystrokes (`fill("")` to clear, then
 * `pressSequentially`), never a bare `fill()`: the custom textarea control only
 * propagates trusted input events to the reactive form, so a programmatic fill
 * changes the DOM value while the form stays pristine and the save button never
 * enables.
 */
test.describe("admin instance settings", () => {
  async function openSettings(page: Page): Promise<void> {
    await login(page);
    await page.goto("/admin/settings");
    await expect(page.getByRole("heading", { name: "Настройки" })).toBeVisible();
    // The editor stays disabled until the settings load, so an enabled field
    // also proves the initial GET landed.
    await expect(page.locator("#system-prompt")).toBeEnabled();
  }

  function patchResponse(page: Page) {
    return page.waitForResponse(
      (resp) =>
        resp.url().includes("/api/v1/admin/settings") &&
        resp.request().method() === "PATCH",
    );
  }

  async function typeInto(locator: Locator, value: string) {
    await locator.fill("");
    await locator.pressSequentially(value);
  }

  test("updates the system prompt extension and keeps it after reload", async ({
    page,
    browser,
  }) => {
    await openSettings(page);

    const promptField = page.locator("#system-prompt");
    const previous = await promptField.inputValue();
    const updated = `e2e-дополнение ${Date.now()}`;

    await typeInto(promptField, updated);
    const saveButton = page
      .locator(".settings-card", { hasText: "Системный промпт" })
      .getByRole("button", { name: "Сохранить" });
    await expect(saveButton).toBeEnabled();

    const saved = patchResponse(page);
    await saveButton.click();
    expect((await saved).ok()).toBe(true);
    await expect(page.getByText("Системный промпт сохранён")).toBeVisible();

    await page.reload();
    await expect(page.locator("#system-prompt")).toHaveValue(updated);

    // Restore through the API: the UI requires non-blank text, so a blank
    // previous value cannot be saved back through the form. The request fixture
    // has its own cookie jar, so the call goes through a context carrying the
    // logged-in storage state.
    const api = await browser.newContext({
      storageState: await page.context().storageState(),
    });
    try {
      const restored = await api.request.patch("/api/v1/admin/settings", {
        data: { prompt_extension: previous },
      });
      expect(restored.ok()).toBe(true);
    } finally {
      await api.close();
    }
  });

  test("rejects an unknown setting key with a 422", async ({ page }) => {
    await openSettings(page);

    const api = await page.context().request;
    const response = await api.patch("/api/v1/admin/settings", {
      data: { laser_speed_m_per_hour: 9 },
    });

    expect(response.status()).toBe(422);
  });

  test("PUT is no longer a settings verb", async ({ page }) => {
    await openSettings(page);

    const response = await page.context().request.put("/api/v1/admin/settings", {
      data: { prompt_extension: "через PUT" },
    });

    expect(response.status()).toBe(405);
  });
});
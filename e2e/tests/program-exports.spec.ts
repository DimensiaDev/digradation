import { statSync } from "node:fs";

import { expect, login, test, type Page } from "./helpers";

/**
 * The two forms a finished programme leaves the system as.
 *
 * The document and the slides are the mandatory outputs of the case, and they
 * are attachments like any other: the state carries their ids, the client asks
 * the attachment route for a link, and the browser fetches it from object
 * storage. Every link of that chain is real here — the row, the stored object,
 * the presigned URL and the download — because a broken one looks the same from
 * the UI: a button that does nothing. The stack seeds them on the `approved`
 * programme it writes.
 */

const FINISHED_TITLE = "Agile-трансформация команд";

/** Open the seeded programme that already has its exports. */
async function openFinishedProgramme(page: Page): Promise<void> {
  await page.goto("/programs");
  await page.getByRole("button", { name: `Открыть программу «${FINISHED_TITLE}»` }).click();
  await page.getByRole("button", { name: "Открыть программу", exact: true }).click();

  await expect(page).toHaveURL(/\/chats\/[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: "Результаты программы" })).toBeVisible();
}

/** Download through the button and answer with what the browser received. */
async function downloadVia(page: Page, buttonName: RegExp) {
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 20_000 }),
    page.getByRole("button", { name: buttonName }).click()
  ]);
  return download;
}

test.describe("programme exports", () => {
  test("offers both forms and downloads the document", async ({ page }) => {
    await login(page);
    await openFinishedProgramme(page);

    await expect(page.getByRole("button", { name: /Скачать DOCX/ })).toBeEnabled();
    await expect(page.getByRole("button", { name: /Скачать PPTX/ })).toBeEnabled();

    const document = await downloadVia(page, /Скачать DOCX/);

    // The name comes from the attachment the backend serves, not from the client's
    // own guess, so it is the export the programme was written as.
    expect(document.suggestedFilename()).toBe("programme.docx");

    // A zero-byte file would pass a name check while proving nothing: the bytes
    // travelled from object storage through the presigned URL.
    const path = await document.path();
    expect(path).not.toBeNull();
    expect(statSync(path as string).size).toBeGreaterThan(0);

    // The button is usable again, so a second download is not blocked.
    await expect(page.getByRole("button", { name: /Скачать DOCX/ })).toBeEnabled();
  });

  test("downloads the slides as their own file", async ({ page }) => {
    await login(page);
    await openFinishedProgramme(page);

    const slides = await downloadVia(page, /Скачать PPTX/);

    expect(slides.suggestedFilename()).toBe("programme-slides.pptx");
    const path = await slides.path();
    expect(path).not.toBeNull();
    expect(statSync(path as string).size).toBeGreaterThan(0);
  });

  test("keeps the exports out of the uploads the methodist staged", async ({ page }) => {
    await login(page);
    await openFinishedProgramme(page);

    // The exports are attachments with no message, so a client that renders the
    // session's files would offer them as pending uploads — and let the methodist
    // delete the programme's own document.
    await page.getByRole("button", { name: /^Файлы/ }).click();
    await expect(page.getByText("Нет файлов в загрузке")).toBeVisible();
    await expect(page.locator(".session-files__file", { hasText: "programme.docx" })).toHaveCount(0);
  });
});

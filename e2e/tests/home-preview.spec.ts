import { expect, login, test } from "./helpers";

/** A 1x1 PNG; the preview only needs a decodable image. */
const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z1ZkAAAAASUVORK5CYII=",
  "base64",
);

test.describe("home file preview", () => {
  test("repeated previews do not grow the home body", async ({ page }) => {
    await login(page);
    await page.goto("/home");
    await page.locator('input[type="file"]').setInputFiles({
      name: "photo.png",
      mimeType: "image/png",
      buffer: PNG_BYTES,
    });

    const tile = page.locator(".file-tile__media");
    await expect(tile).toBeVisible();
    await page.waitForTimeout(500);

    const body = page.locator(".home__body");
    const metrics = () =>
      body.evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        scrollHeight: element.scrollHeight,
      }));
    const before = await metrics();

    for (let attempt = 0; attempt < 3; attempt++) {
      await tile.click();
      await expect(page.locator("dialog.file-preview")).toBeVisible();
      await page.getByRole("button", { name: "Закрыть предпросмотр" }).click();
      await expect(page.locator("dialog.file-preview")).toHaveCount(0);
    }
    await page.waitForTimeout(300);

    const after = await metrics();
    expect(after.scrollWidth).toBe(after.clientWidth);
    expect(after.scrollHeight).toBeLessThanOrEqual(before.scrollHeight + 1);
  });
});

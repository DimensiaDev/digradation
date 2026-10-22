import { expect, login, test, type Page } from "./helpers";

/**
 * The decision panel over a rendered draft.
 *
 * `POST /program/approve` and `POST /program/revision` are the methodist's own
 * controls, and the panel that reaches them is rendered by the app alone, so
 * only a browser driving the real stack can prove it is there at all. It opens
 * the programme the stack seeds as `pending_approval` — the state a rendered
 * draft is in.
 *
 * The two requests are answered locally. A real revision starts a stage, and the
 * offline provider the stack runs cannot save a stage block, so the run would
 * fail and the seeded programme would stop being a draft for the rest of the
 * suite. What is under test here is the client: the request it builds, the shape
 * it sends, and the panel's own behaviour, none of which needs the stage.
 */

const PENDING_TITLE = "Основы машинного обучения";

/** Open the seeded programme that waits for the methodist's decision. */
async function openPendingProgramme(page: Page): Promise<void> {
  await page.goto("/programs");
  await page.getByRole("button", { name: `Открыть программу «${PENDING_TITLE}»` }).click();
  await page.getByRole("button", { name: "Открыть программу", exact: true }).click();

  await expect(page).toHaveURL(/\/chats\/[0-9a-f-]+$/);
  await expect(page.locator(".decision")).toBeVisible();
}

/** Answer both decision endpoints locally, recording what the client sent. */
async function stubDecisions(page: Page): Promise<Array<{ url: string; body: unknown }>> {
  const posted: Array<{ url: string; body: unknown }> = [];

  for (const endpoint of ["approve", "revision"]) {
    await page.route(`**/program/${endpoint}`, (route) => {
      posted.push({ url: route.request().url(), body: route.request().postDataJSON() });
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });
  }

  return posted;
}

test.describe("programme decision panel", () => {
  test("takes the composer's place and offers both decisions", async ({ page }) => {
    await login(page);
    await openPendingProgramme(page);

    const panel = page.locator(".decision");
    await expect(panel).toContainText("Каркас программы готов");
    await expect(panel.getByRole("button", { name: "Утвердить каркас" })).toBeEnabled();
    await expect(panel.getByRole("button", { name: "Выбрать блок" })).toBeEnabled();

    // The decision owns the composer's slot, so no message can be typed while the
    // run waits for it.
    await expect(page.locator(".chat-input-panel__form")).toHaveCount(0);
  });

  test("reads at the size of the app's own text", async ({ page }) => {
    await login(page);
    await openPendingProgramme(page);

    const sizes = await page.locator(".decision").evaluate((panel) => {
      const size = (selector: string) => {
        const element = panel.querySelector(selector);
        return element ? parseFloat(getComputedStyle(element).fontSize) : 0;
      };

      return {
        body: parseFloat(getComputedStyle(document.body).fontSize),
        eyebrow: size(".decision__eyebrow"),
        cardTitle: size(".decision__card-title"),
        cardText: size(".decision__card-text"),
        hint: size(".decision__hint"),
        action: size(".decision__action")
      };
    });

    // The panel is read next to the chat, whose own text is the body size, so its
    // headings match it and nothing in it drops below the smallest step that text
    // uses for secondary lines. It shipped two steps down (11px and 12px) once,
    // which is what this pins.
    expect(sizes.eyebrow).toBeGreaterThanOrEqual(sizes.body - 1);
    expect(sizes.cardTitle).toBeGreaterThanOrEqual(sizes.body - 1);
    expect(Math.min(sizes.hint, sizes.cardText, sizes.action)).toBeGreaterThanOrEqual(14);
  });

  test("shows one card at a time and comes back on cancel", async ({ page }) => {
    await login(page);
    await openPendingProgramme(page);

    const cards = page.locator(".decision__card");
    await expect(cards).toHaveCount(2);

    await page.getByRole("button", { name: "Выбрать блок" }).click();

    await expect(cards).toHaveCount(1);
    await expect(page.locator(".decision")).not.toContainText("Агент напишет оценочные средства");
    // The picker holds the blocks that exist, in the order the pipeline writes them.
    await expect(page.locator(".decision__block")).toHaveText([
      "Компетенции",
      "Учебный план и часы",
      "Источники",
      "Паспорт программы"
    ]);

    await page.getByRole("button", { name: "Отмена" }).click();
    await expect(cards).toHaveCount(2);
    await expect(page.locator(".decision__blocks")).toHaveCount(0);
  });

  test("puts the two actions on one column", async ({ page }) => {
    await login(page);
    await openPendingProgramme(page);

    const boxes = await page.locator(".decision__action").evaluateAll((elements) =>
      elements.map((element) => {
        const box = element.getBoundingClientRect();
        return { left: Math.round(box.left), width: Math.round(box.width) };
      })
    );

    expect(boxes).toHaveLength(2);
    expect(new Set(boxes.map((box) => box.left)).size).toBe(1);
    expect(new Set(boxes.map((box) => box.width)).size).toBe(1);
  });

  test("approves through the endpoint the backend owns", async ({ page }) => {
    await login(page);
    await openPendingProgramme(page);
    const posted = await stubDecisions(page);

    await page.getByRole("button", { name: "Утвердить каркас" }).click();

    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0].url).toMatch(/\/program\/approve$/);
    expect(posted[0].body).toEqual({});
    await expect(page.getByText("Каркас утверждён")).toBeVisible();
  });

  test("sends the chosen block and the notes to the revision endpoint", async ({ page }) => {
    await login(page);
    await openPendingProgramme(page);
    const posted = await stubDecisions(page);

    await page.getByRole("button", { name: "Выбрать блок" }).click();
    await page.getByRole("radio", { name: "Учебный план и часы" }).click();
    await page.locator('textarea[placeholder="Что именно изменить…"]').fill("Замени тему 2.3");

    // The notes are what makes a revision submittable at all.
    const submit = page.getByRole("button", { name: "На доработку" });
    await expect(submit).toBeEnabled();
    await submit.click();

    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0].url).toMatch(/\/program\/revision$/);
    expect(posted[0].body).toEqual({ stage: "hours", notes: "Замени тему 2.3" });
    await expect(page.getByText("Программа отправлена на доработку")).toBeVisible();
  });
});

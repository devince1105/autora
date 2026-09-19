// Phase 5 in a real browser (T-520): a story from the demo newsroom's feeds goes through the whole
// line in simulation (the stack's worker, the fake model, fixture tools). From the 3D office, the
// writer's panel links to its draft, and the draft page shows what the writer's task wrote; then a
// person approves in the inbox and the public site shows the article in both languages.
import { expect, test, type Page } from "@playwright/test";

import type {} from "../src/office3d/perf/StatsProbe"; // window.__autoraOffice
import { API_URL, Stack, TOKEN } from "./stack";

test.describe.configure({ mode: "serial" });

let stack: Stack;

test.beforeAll(async () => {
  stack = await Stack.start();
});

test.afterAll(async () => {
  await stack?.stop();
});

test.beforeEach(async ({ context }) => {
  await context.addInitScript((token) => window.localStorage.setItem("autora.operatorToken", token), TOKEN);
});

const panel = (page: Page) => page.locator('[role="dialog"][aria-label$="的詳細資訊"]');
const auth = { Authorization: `Bearer ${TOKEN}` };

async function api<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`${API_URL}${path}`, { headers: auth });
  expect(response.ok(), `${path}: ${response.status()}`).toBe(true);
  return (await response.json()) as T;
}

interface Article {
  id: string;
  slug: string;
  title: string;
  shown: number;
  story_title: string;
  public_urls: Record<string, string>;
  languages: Record<string, { title: string; blocks: { type: string; text: string; claim_ids: string[] }[] }>;
}

test("a story from the feeds to the public site, and from the office to its draft", async ({ page }, info) => {
  const company = stack.newsroomCompanyId;

  // 1. the story page: the demo feeds were read and clustered; start the microgrid story
  await page.goto(`/newsroom/stories?company=${company}`);
  await page.getByRole("link", { name: /microgrid/i }).first().click();
  await expect(page).toHaveURL(/\/newsroom\/stories\/[0-9a-f-]+$/);
  const storyTitle = (await page.getByRole("heading", { level: 1 }).textContent())!;
  await page.getByRole("button", { name: "開始製作" }).click();
  await expect(page.getByText("製作中")).toBeVisible({ timeout: 15_000 });

  // 2. the worker runs research, analysis, draft and review; the line stops at the approval
  await expect
    .poll(async () => (await api<unknown[]>(page, `/api/approvals?company_id=${company}&state=PENDING`)).length, {
      timeout: 120_000,
    })
    .toBe(1);
  // the story page's timeline shows the workflow
  await page.reload();
  await expect(page.locator("#timeline").getByText("工作流程建立")).toBeVisible();

  // 3. the office: click the writer, follow its panel's link to the draft
  await page.goto(`/office?company=${company}`);
  await expect(page.locator("[data-office-mode]")).not.toHaveAttribute("data-office-mode", "detecting", { timeout: 30_000 });
  const mode = await page.locator("[data-office-mode]").getAttribute("data-office-mode");
  if (mode === "3d") {
    await page.waitForFunction(() => (window.__autoraOffice?.frames ?? 0) > 0, null, { timeout: 90_000 });
    const tag = page.getByTestId(/^head-tag-/).filter({ hasText: "Wren" });
    await expect(tag).toBeVisible({ timeout: 30_000 });
    const box = (await tag.boundingBox())!;
    await page.mouse.click(box.x + box.width / 2, box.y + box.height + 15);
  } else {
    await page.locator('[data-testid^="board-agent-"]').filter({ hasText: "Wren" }).click();
  }
  await expect(panel(page)).toHaveAttribute("aria-label", "Wren 的詳細資訊");
  // the panel's task is the writer's task for this story
  await expect(panel(page)).toContainText(`撰稿：${storyTitle}`);
  await page.screenshot({ path: info.outputPath("writer-panel.png") });
  const draftLink = panel(page).getByRole("link", { name: /^文章草稿 v\d+$/ });
  await expect(draftLink).toBeVisible();
  const href = (await draftLink.getAttribute("href"))!;
  await draftLink.click();

  // 4. the draft page shows what the writer's task wrote (the API's copy of the same version)
  await expect(page).toHaveURL(new RegExp(`${href.replace("?", "\\?")}$`));
  const [, articleId, version] = href.match(/\/newsroom\/articles\/([0-9a-f-]+)\?version=(\d+)/)!;
  const article = await api<Article>(page, `/api/articles/${articleId}?version=${version}`);
  expect(article.shown).toBe(Number(version));
  expect(article.story_title).toBe(storyTitle);
  const zh = article.languages["zh-TW"]!;
  const text = page.getByRole("article");
  await expect(text.getByRole("heading", { level: 2 })).toHaveText(zh.title);
  for (const block of zh.blocks.filter((b) => b.type === "paragraph")) {
    await expect(text.getByText(block.text, { exact: false })).toBeVisible();
  }
  // every paragraph's claim marker leads to the claim, with its quote in place
  const marker = text.locator('a[href^="#claim-"]').first();
  const claimId = (await marker.getAttribute("href"))!.slice("#claim-".length);
  await marker.click();
  const claim = page.locator(`#claim-${claimId}`);
  await claim.locator("summary").click();
  await expect(claim.locator("mark").first()).toBeVisible();
  await expect(page.locator("#fact-check").getByText("通過", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("draft-page.png"), fullPage: true });

  // 5. a person approves in the inbox; the article is published, marketing drafts its posts
  await page.goto(`/approvals?company=${company}`);
  await page.getByRole("button", { name: "核准" }).click();
  await expect
    .poll(async () => (await api<Article>(page, `/api/articles/${articleId}`)).public_urls["en"] ?? null, {
      timeout: 60_000,
    })
    .toBe(`/en/articles/${article.slug}`);
  await expect
    .poll(async () => {
      const detail = await api<{ distributions: { channel: string }[] }>(page, `/api/articles/${articleId}`);
      return detail.distributions.map((d) => d.channel).sort();
    }, { timeout: 60_000 })
    .toEqual(["site", "social_draft"]);

  // 6. the public site, in both languages
  await page.goto(`/zh-TW/articles/${article.slug}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(zh.title);
  await page.getByRole("link", { name: "English" }).last().click();
  await expect(page).toHaveURL(new RegExp(`/en/articles/${article.slug}$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(article.languages["en"]!.title);
  await page.screenshot({ path: info.outputPath("public-en.png") });
});

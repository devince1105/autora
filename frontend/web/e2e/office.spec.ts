// The office in a real browser. T-401: 3D on a desktop with WebGL 2; the 2D board when asked for,
// on a narrow screen, or without WebGL 2; a lost WebGL context shows an overlay and is rebuilt on
// request. T-411 (Phase 4 acceptance, 09): click an avatar and its panel shows the live state
// within 300 ms; during a run the head badges move in order and hand-offs are walked; the 2D
// board opens the same panel.
import { expect, test, type Page } from "@playwright/test";

import type {} from "../src/office3d/perf/StatsProbe"; // window.__autoraOffice
import type {} from "../src/office3d/visual/CueRunner"; // window.__autoraOfficeCues
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

const office = (page: Page) => page.locator("[data-office-mode]");
// Next's route announcer is also role=alert: find ours by its text.
const paused = (page: Page) => page.getByRole("alert").filter({ hasText: "3D 暫停" });

async function open(page: Page, query = ""): Promise<void> {
  await page.goto(`/office?company=${stack.companyId}${query}`);
  await expect(office(page)).not.toHaveAttribute("data-office-mode", "detecting", { timeout: 30_000 });
}

test("desktop: a WebGL 2 canvas that draws", async ({ page }, info) => {
  await open(page);
  await expect(office(page)).toHaveAttribute("data-office-mode", "3d");
  const canvas = page.locator('[data-office-mode="3d"] canvas');
  await expect(canvas).toHaveCount(1, { timeout: 30_000 });
  const state = await canvas.evaluate((el: HTMLCanvasElement) => {
    const gl = el.getContext("webgl2");
    return { webgl2: gl !== null, lost: gl?.isContextLost() ?? true };
  });
  expect(state).toMatchObject({ webgl2: true, lost: false });
  // sized to its container once measured (300 x 150 is a canvas's default size)
  await expect.poll(() => canvas.evaluate((el: HTMLCanvasElement) => el.width)).toBeGreaterThan(300);

  // The in-canvas probe: frames are drawn, instancing keeps draw calls low, the scene is low-poly.
  // Its first report can say 0 fps under CI's software WebGL (shaders still compiling in that
  // second): wait for one with frames in it.
  const stats = await page.waitForFunction(() => ((window.__autoraOffice?.fps ?? 0) > 0 ? window.__autoraOffice : null), null, {
    timeout: 60_000,
  });
  const { fps, drawCalls, triangles } = (await stats.jsonValue())!;
  info.annotations.push({ type: "office stats", description: JSON.stringify({ fps, drawCalls, triangles }) });
  expect(fps).toBeGreaterThan(0);
  // the static office is one merged mesh: a few dozen calls at most, shadow pass included
  expect(drawCalls).toBeLessThan(60);
  // renderer counts include the shadow pass; the scene's own budget (< 40k) is in kit.test.ts
  expect(triangles).toBeLessThan(100_000);
  await page.screenshot({ path: info.outputPath("office-3d.png") });
});

test("a lost WebGL context: overlay, then rebuilt on request", async ({ page }) => {
  await open(page);
  const canvas = page.locator('[data-office-mode="3d"] canvas');
  await expect(canvas).toHaveCount(1, { timeout: 30_000 });
  // once the scene draws (the canvas and its listeners are set up), lose the context
  await page.waitForFunction(() => window.__autoraOffice ?? null, null, { timeout: 15_000 });
  await canvas.evaluate((el: HTMLCanvasElement) => el.getContext("webgl2")!.getExtension("WEBGL_lose_context")!.loseContext());
  await expect(paused(page)).toBeVisible();
  await expect(office(page)).toHaveAttribute("data-context-lost", "true");

  await page.getByRole("button", { name: "重新建立 3D" }).click();
  await expect(paused(page)).toHaveCount(0);
  await expect(canvas).toHaveCount(1);
  expect(await canvas.evaluate((el: HTMLCanvasElement) => el.getContext("webgl2")!.isContextLost())).toBe(false);
});

test("?view=2d, a narrow screen, or no WebGL 2: the 2D board with the company's agents", async ({ page, browser }, info) => {
  await open(page, "&view=2d");
  await expect(office(page)).toHaveAttribute("data-office-mode", "2d");
  await expect(page.locator('[data-testid^="board-agent-"]')).toHaveCount(3, { timeout: 15_000 });
  await page.screenshot({ path: info.outputPath("office-2d.png") });

  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await phone.addInitScript((token) => window.localStorage.setItem("autora.operatorToken", token), TOKEN);
  const small = await phone.newPage();
  await open(small);
  await expect(office(small)).toHaveAttribute("data-office-mode", "2d");
  await expect(small.getByText("螢幕較窄")).toBeVisible();
  await phone.close();

  const noGl = await browser.newContext();
  await noGl.addInitScript((token) => {
    window.localStorage.setItem("autora.operatorToken", token);
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      return type === "webgl2" ? null : (original as (...a: unknown[]) => unknown).call(this, type, ...rest);
    } as typeof original;
  }, TOKEN);
  const plain = await noGl.newPage();
  await open(plain, "&view=3d");
  await expect(office(plain)).toHaveAttribute("data-office-mode", "2d");
  await expect(plain.getByText(/不支援 WebGL 2/)).toBeVisible();
  await noGl.close();
});

const panel = (page: Page) => page.locator('[role="dialog"][aria-label$="的詳細資訊"]');

/**
 * The Phase 4 limit is 300 ms on real hardware. CI draws with software WebGL (SwiftShader), where
 * one frame of this scene takes hundreds of milliseconds and holds the main thread, so there the
 * bound only guards against a hang.
 */
const PANEL_LIMIT_MS = process.env.CI ? 3000 : 300;

async function openOffice(page: Page, query = ""): Promise<void> {
  await open(page, query);
  // the scene has drawn (the probe reports after its first second of frames)
  await page.waitForFunction(() => (window.__autoraOffice?.frames ?? 0) > 0, null, { timeout: 90_000 });
}

test("click an avatar: its panel shows the live state within 300 ms (Phase 4 AC)", async ({ page }, info) => {
  await openOffice(page);
  // measured in the page: from the pointer going down to the panel being in the DOM
  await page.evaluate(() => {
    const timing = { down: 0, shown: 0 };
    (window as unknown as { __panelTiming: typeof timing }).__panelTiming = timing;
    window.addEventListener("pointerdown", () => void (timing.down = performance.now()), { capture: true });
    new MutationObserver(() => {
      if (!timing.shown && document.querySelector('[role="dialog"][aria-label$="的詳細資訊"]')) timing.shown = performance.now();
    }).observe(document.body, { childList: true, subtree: true });
  });
  // a seated, idle agent (a recent run leaves "done" agents standing behind their chairs for 20 s)
  const tag = page.getByTestId(/^head-tag-/).first();
  await expect(tag).toContainText("閒置", { timeout: 40_000 });
  const name = (await tag.locator("span").first().textContent())!.replace(/閒置$/, "");
  const box = (await tag.boundingBox())!;
  // the figure's middle (its click box spans from the tag down to the seat, ~40 px at 1280 x 720)
  await page.mouse.click(box.x + box.width / 2, box.y + box.height + 15);
  await expect(panel(page)).toBeVisible();
  const { down, shown } = await page.evaluate(() => (window as unknown as { __panelTiming: { down: number; shown: number } }).__panelTiming);
  info.annotations.push({ type: "panel ms", description: String(Math.round(shown - down)) });
  expect(shown - down).toBeLessThan(PANEL_LIMIT_MS);
  await page.screenshot({ path: info.outputPath("office-panel.png") });
  await expect(panel(page)).toHaveAttribute("aria-label", `${name} 的詳細資訊`);
  // the live tab: the state from the store (the same label as the head badge)
  await expect(panel(page).getByText("目前任務")).toBeVisible();
  await expect(panel(page).getByText("閒置")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel(page)).toHaveCount(0);
});

test("a run in the office: badges move in order, hand-offs are walked, the strip counts", async ({ page, request }) => {
  await openOffice(page);
  await expect(page.getByTestId("mini-agents")).toContainText("0 / 3");
  // log every head-tag change and every walk, in the page
  await page.evaluate(() => {
    const log: { text: string; t: number }[] = [];
    (window as unknown as { __tagLog: typeof log }).__tagLog = log;
    const record = () => {
      for (const el of document.querySelectorAll('[data-testid^="head-tag-"] > span:first-child')) {
        const text = el.textContent ?? "";
        if (log.findLast((e) => e.text.startsWith(text.slice(0, 3)))?.text !== text) log.push({ text, t: performance.now() });
      }
    };
    new MutationObserver(record).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
  });
  const res = await request.post(`${API_URL}/api/companies/${stack.companyId}/workflows`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    // the analyst's reply is slow: one busy spell long enough to be seen at any frame rate. Head
    // tags update in the frame loop, and under CI's software rendering one frame of the full
    // office (T-413) can take seconds; 3 s and then 6 s spells were each missed there once.
    data: {
      template: "echo.chain_v1",
      project_id: stack.projectId,
      params: { topic: "office e2e", pause: { task: "echo_analyze", attempt: 1, seconds: process.env.CI ? 15 : 3 } },
    },
  });
  expect(res.status()).toBe(201);

  // everyone done: the writer's badge says so
  await expect(page.getByTestId(/^head-tag-/).filter({ hasText: "Wren" })).toContainText("已完成", { timeout: 60_000 });
  const log = await page.evaluate(() => (window as unknown as { __tagLog: { text: string; t: number }[] }).__tagLog);
  const first = (name: string, states: string[]) => log.find((e) => e.text.startsWith(name) && states.some((s) => e.text.endsWith(s)))?.t ?? Infinity;
  const busy = ["思考中", "工作中", "檢查中"];
  // Tags update in the frame loop. On real hardware every busy spell shows; under CI's software
  // rendering a frame can outlast a quick step, so there the analyst's long spell must show.
  const worked = ["Rae", "Ana", "Wren"].filter((name) => first(name, busy) < first(name, ["已完成"]));
  expect(worked).toEqual(process.env.CI ? expect.arrayContaining(["Ana"]) : ["Rae", "Ana", "Wren"]);
  // done in order (never out of it; two can land in the same slow frame)
  const done = ["Rae", "Ana", "Wren"].map((name) => first(name, ["已完成"]));
  expect(done.every(Number.isFinite)).toBe(true);
  expect(done[0]).toBeLessThanOrEqual(done[1]);
  expect(done[1]).toBeLessThanOrEqual(done[2]);
  // two hand-offs (researcher -> analyst, analyst -> writer), each walked
  await expect.poll(() => page.evaluate(() => window.__autoraOfficeCues?.walks ?? 0), { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
  // the strip follows the store and the API
  await expect(page.getByTestId("mini-agents")).toContainText("/ 3");
  await expect(page.getByTestId("mini-tasks")).toContainText(/\d/);
});

test("the 2D board opens the same panel", async ({ page }) => {
  await open(page, "&view=2d");
  const card = page.locator('[data-testid^="board-agent-"]').first();
  const name = await card.locator("span.font-semibold").first().textContent();
  await card.click();
  await expect(panel(page)).toHaveAttribute("aria-label", `${name} 的詳細資訊`);
  await page.getByRole("button", { name: "關閉" }).click();
  await expect(panel(page)).toHaveCount(0);
});

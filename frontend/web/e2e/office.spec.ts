// T-401: the office canvas in a real browser. 3D on a desktop with WebGL 2; the 2D board when
// asked for, on a narrow screen, or without WebGL 2; a lost WebGL context shows an overlay and
// is rebuilt on request.
import { expect, test, type Page } from "@playwright/test";

import type {} from "../src/office3d/perf/StatsProbe"; // window.__autoraOffice
import { Stack, TOKEN } from "./stack";

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
  const stats = await page.waitForFunction(() => window.__autoraOffice ?? null, null, { timeout: 15_000 });
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

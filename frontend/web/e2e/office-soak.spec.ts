// Phase 4 acceptance, the long part (T-412, 09): the office open for hours with a simulated run
// every few minutes — memory must not keep growing (heap after GC, end vs start < 50 MB) and it
// must keep drawing at >= 30 FPS. Real browser (local Chrome, real GPU), real stack.
//
// **It watches the company that has an org chart** (the demo newsroom), because that is the
// office T-600 built: departments as zones, business colour bands, and rooms you can step into.
// Measuring the three-desk echo company would measure a scene nobody looks at any more.
//
// **It steps in and out of a department every sample.** Entering a room unmounts the head tags of
// everyone else and mounts them again on the way out — hundreds of times over two hours. That is
// exactly the shape of thing that leaks, so the soak does it rather than assuming it is free.
//
//   SOAK_MINUTES=120 pnpm -F web e2e office-soak     (or: make soak)
//   SOAK_ROUND_MINUTES=5 (default)  SOAK_SAMPLE_SECONDS=60 (default)
//
// Skipped unless SOAK_MINUTES is set: it is not part of `make e2e` or CI. It writes every sample
// to test-results/.../office-soak.json and a summary to the console.
import { expect, test, type CDPSession, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";

import type {} from "../src/office3d/perf/StatsProbe";
import type {} from "../src/office3d/visual/CueRunner";
import type {} from "../src/realtime/latency";
import { API_URL, Stack, TOKEN } from "./stack";

const MINUTES = Number(process.env.SOAK_MINUTES ?? 0);
const ROUND_MS = Number(process.env.SOAK_ROUND_MINUTES ?? 5) * 60_000;
const SAMPLE_MS = Number(process.env.SOAK_SAMPLE_SECONDS ?? 60) * 1000;
const WARMUP_MS = 60_000;
const HEAP_LIMIT_MB = 50;
const FPS_FLOOR = 30;
const WS_P95_MS = 100; // AC-S7: handling one socket message must stay under this

interface Sample {
  minute: number;
  heapMB: number;
  gcHeapMB: number | null;
  nodes: number;
  listeners: number;
  fps: number;
  drawCalls: number;
  triangles: number;
  walks: number;
  rounds: number;
  /** The department the office is standing in when this sample was taken, if any. */
  room: string | null;
  /** Head tags drawn right now: fewer inside a room, everyone outside it. */
  tags: number;
  /** AC-S7: milliseconds to turn one socket message into state, over the last samples. */
  wsP95: number;
  wsMessages: number;
}

let stack: Stack;

test.skip(!MINUTES, "set SOAK_MINUTES to run the soak test");

test.beforeAll(async () => {
  stack = await Stack.start();
});

test.afterAll(async () => {
  await stack?.stop();
});

async function metrics(cdp: CDPSession): Promise<Record<string, number>> {
  const { metrics } = (await cdp.send("Performance.getMetrics")) as { metrics: { name: string; value: number }[] };
  return Object.fromEntries(metrics.map((m) => [m.name, m.value]));
}

async function heapAfterGc(cdp: CDPSession): Promise<number> {
  for (let i = 0; i < 3; i++) await cdp.send("HeapProfiler.collectGarbage");
  return (await metrics(cdp)).JSHeapUsedSize / 1024 / 1024;
}

async function office(page: Page) {
  return page.evaluate(() => ({
    stats: window.__autoraOffice ?? null,
    walks: window.__autoraOfficeCues?.walks ?? 0,
    ws: window.__autoraRealtime?.stats() ?? null,
  }));
}

type Counted = "nodes" | "listeners";
const least = (rows: Sample[], key: Counted) => Math.min(...rows.map((r) => r[key]));
const most = (rows: Sample[], key: Counted) => Math.max(...rows.map((r) => r[key]));

/** The mean of each quarter of the soak: four numbers that show a drift, or the lack of one. */
function quarterMeans(rows: Sample[], key: Counted): number[] {
  const size = Math.floor(rows.length / 4);
  if (!size) return [];
  return [0, 1, 2, 3].map((q) => {
    const slice = rows.slice(q * size, (q + 1) * size);
    return Math.round(slice.reduce((total, r) => total + r[key], 0) / slice.length);
  });
}

const percentile = (values: number[], p: number) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
};

test("the office stays lean and smooth for hours (Phase 4 AC)", async ({ page, request }, info) => {
  test.setTimeout(MINUTES * 60_000 + WARMUP_MS + 10 * 60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript((token) => window.localStorage.setItem("autora.operatorToken", token), TOKEN);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`/admin/office?company=${stack.newsroomCompanyId}`);
  await expect(page.locator('[data-office-mode="3d"] canvas')).toHaveCount(1, { timeout: 60_000 });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");

  // the rooms come from the org chart, so ask the strip which ones this company has
  const strip = page.getByRole("group", { name: "部門" });
  await expect(strip).toBeVisible({ timeout: 60_000 });
  const rooms = await strip.getByRole("button").evaluateAll((buttons) =>
    buttons
      .map((b) => b.getAttribute("data-testid") ?? "")
      .filter((id) => id.startsWith("department-") && id !== "department-all"),
  );
  expect(rooms.length).toBeGreaterThan(1);

  let rounds = 0;
  const startRound = async () => {
    const res = await request.post(`${API_URL}/api/companies/${stack.newsroomCompanyId}/workflows`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      data: {
        template: "echo.chain_v1",
        project_id: stack.newsroomProjectId,
        params: { topic: `soak ${rounds + 1}` },
      },
    });
    expect(res.status()).toBe(201);
    rounds++;
  };

  /** Step into the next room, then out again on the sample after that. */
  let room: string | null = null;
  const walkTheFloor = async (sampleNo: number) => {
    const next = sampleNo % 2 === 1 ? rooms[(sampleNo >> 1) % rooms.length] : "department-all";
    await page.getByTestId(next).click();
    room = next === "department-all" ? null : next.replace("department-", "");
  };

  await startRound();
  await page.waitForTimeout(WARMUP_MS);
  const baseline = await heapAfterGc(cdp);
  const started = Date.now();
  const samples: Sample[] = [];
  let nextRound = started + ROUND_MS;
  let sampleNo = 0;

  while (Date.now() - started < MINUTES * 60_000) {
    await page.waitForTimeout(Math.max(0, started + (sampleNo + 1) * SAMPLE_MS - Date.now()));
    sampleNo++;
    if (Date.now() >= nextRound) {
      await startRound();
      nextRound += ROUND_MS;
    }
    await walkTheFloor(sampleNo);
    const m = await metrics(cdp);
    const { stats, walks, ws } = await office(page);
    const tags = await page.getByTestId(/^head-tag-/).count();
    // every tenth sample also after a GC: the trend without garbage noise
    const gcHeapMB = sampleNo % 10 === 0 ? await heapAfterGc(cdp) : null;
    const sample: Sample = {
      minute: Math.round(((Date.now() - started) / 60_000) * 10) / 10,
      heapMB: Math.round((m.JSHeapUsedSize / 1024 / 1024) * 10) / 10,
      gcHeapMB: gcHeapMB === null ? null : Math.round(gcHeapMB * 10) / 10,
      nodes: m.Nodes,
      listeners: m.JSEventListeners,
      fps: stats?.fps ?? 0,
      drawCalls: stats?.drawCalls ?? 0,
      triangles: stats?.triangles ?? 0,
      walks,
      rounds,
      room,
      tags,
      wsP95: ws?.p95 ?? 0,
      wsMessages: ws?.count ?? 0,
    };
    samples.push(sample);
    console.log(`[soak] ${JSON.stringify(sample)}`);
  }

  // back to the whole floor, so the end is measured in the same state as the start
  await page.getByTestId("department-all").click();
  room = null;
  // let the last round finish, then measure the heap the same way as at the start
  await page.waitForTimeout(90_000);
  const final = await heapAfterGc(cdp);
  const fps = samples.map((s) => s.fps);
  const floor = samples.filter((s) => s.room === null);
  const wsFinal = (await office(page)).ws;
  const summary = {
    minutes: MINUTES,
    rounds,
    walks: (await office(page)).walks,
    heap: { baselineMB: +baseline.toFixed(1), finalMB: +final.toFixed(1), growthMB: +(final - baseline).toFixed(1), limitMB: HEAP_LIMIT_MB },
    fps: { min: Math.min(...fps), p10: percentile(fps, 10), median: percentile(fps, 50), floor: FPS_FLOOR },
    ws: { p95: wsFinal?.p95 ?? 0, p50: wsFinal?.p50 ?? 0, max: wsFinal?.max ?? 0, messages: wsFinal?.count ?? 0, events: wsFinal?.events ?? 0, limitMs: WS_P95_MS },
    rooms: {
      entered: samples.filter((s) => s.room !== null).length,
      visited: [...new Set(samples.map((s) => s.room).filter(Boolean))],
      tagsInside: Math.max(...samples.filter((s) => s.room !== null).map((s) => s.tags), 0),
      tagsOutside: Math.max(...samples.filter((s) => s.room === null).map((s) => s.tags), 0),
    },
    // DOM nodes and listeners rise inside a room and fall again outside it, so read the samples
    // taken in the same state — the whole floor. And read the *trend*, not the first and last
    // sample: both numbers swing by a third between consecutive samples (head tags and panels
    // mount and unmount), so first-vs-last says whichever story the two endpoints happened to
    // land on. Quarter means show whether it is drifting up; the band shows how noisy it is.
    nodes: { quarters: quarterMeans(floor, "nodes"), min: least(floor, "nodes"), max: most(floor, "nodes") },
    listeners: {
      quarters: quarterMeans(floor, "listeners"),
      min: least(floor, "listeners"),
      max: most(floor, "listeners"),
    },
    pageErrors: errors,
  };
  console.log(`[soak] summary ${JSON.stringify(summary)}`);
  writeFileSync(info.outputPath("office-soak.json"), JSON.stringify({ summary, samples }, null, 2));

  expect(errors).toEqual([]);
  expect(rounds).toBeGreaterThanOrEqual(Math.floor(MINUTES / (ROUND_MS / 60_000)));
  // the rooms were really stepped into: fewer people drawn inside one than on the whole floor
  expect(summary.rooms.entered).toBeGreaterThan(0);
  expect(summary.rooms.tagsInside).toBeLessThan(summary.rooms.tagsOutside);
  expect(final - baseline).toBeLessThan(HEAP_LIMIT_MB);
  expect(percentile(fps, 10)).toBeGreaterThanOrEqual(FPS_FLOOR);
  // AC-S7: the events arrived and were handled quickly, for the whole soak
  expect(wsFinal?.count ?? 0).toBeGreaterThan(0);
  expect(wsFinal?.p95 ?? Infinity).toBeLessThan(WS_P95_MS);
});

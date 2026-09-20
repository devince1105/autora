// Phase 4 acceptance, the long part (T-412, 09): the office open for hours with a simulated run
// every few minutes — memory must not keep growing (heap after GC, end vs start < 50 MB) and it
// must keep drawing at >= 30 FPS. Real browser (local Chrome, real GPU), real stack.
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
  await page.goto(`/office?company=${stack.companyId}`);
  await expect(page.locator('[data-office-mode="3d"] canvas')).toHaveCount(1, { timeout: 60_000 });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");

  let rounds = 0;
  const startRound = async () => {
    const res = await request.post(`${API_URL}/api/companies/${stack.companyId}/workflows`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      data: { template: "echo.chain_v1", project_id: stack.projectId, params: { topic: `soak ${rounds + 1}` } },
    });
    expect(res.status()).toBe(201);
    rounds++;
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
    const m = await metrics(cdp);
    const { stats, walks, ws } = await office(page);
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
      wsP95: ws?.p95 ?? 0,
      wsMessages: ws?.count ?? 0,
    };
    samples.push(sample);
    console.log(`[soak] ${JSON.stringify(sample)}`);
  }

  // let the last round finish, then measure the heap the same way as at the start
  await page.waitForTimeout(90_000);
  const final = await heapAfterGc(cdp);
  const fps = samples.map((s) => s.fps);
  const wsFinal = (await office(page)).ws;
  const summary = {
    minutes: MINUTES,
    rounds,
    walks: (await office(page)).walks,
    heap: { baselineMB: +baseline.toFixed(1), finalMB: +final.toFixed(1), growthMB: +(final - baseline).toFixed(1), limitMB: HEAP_LIMIT_MB },
    fps: { min: Math.min(...fps), p10: percentile(fps, 10), median: percentile(fps, 50), floor: FPS_FLOOR },
    ws: { p95: wsFinal?.p95 ?? 0, p50: wsFinal?.p50 ?? 0, max: wsFinal?.max ?? 0, messages: wsFinal?.count ?? 0, events: wsFinal?.events ?? 0, limitMs: WS_P95_MS },
    nodes: { first: samples[0]?.nodes, last: samples.at(-1)?.nodes },
    listeners: { first: samples[0]?.listeners, last: samples.at(-1)?.listeners },
    pageErrors: errors,
  };
  console.log(`[soak] summary ${JSON.stringify(summary)}`);
  writeFileSync(info.outputPath("office-soak.json"), JSON.stringify({ summary, samples }, null, 2));

  expect(errors).toEqual([]);
  expect(rounds).toBeGreaterThanOrEqual(Math.floor(MINUTES / (ROUND_MS / 60_000)));
  expect(final - baseline).toBeLessThan(HEAP_LIMIT_MB);
  expect(percentile(fps, 10)).toBeGreaterThanOrEqual(FPS_FLOOR);
  // AC-S7: the events arrived and were handled quickly, for the whole soak
  expect(wsFinal?.count ?? 0).toBeGreaterThan(0);
  expect(wsFinal?.p95 ?? Infinity).toBeLessThan(WS_P95_MS);
});

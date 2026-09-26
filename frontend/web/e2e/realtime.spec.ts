// Phase 3 acceptance (T-315, 3d-office/09): two Dashboard tabs show the agent cards changing
// together; the API is down for 20 s and comes back, the page recovers by itself, its last seq
// equals the server's head, and the trace of the run that finished meanwhile has every event.
// The event timeline (/admin/timeline) is the witness for "every event, exactly once": one row per
// event, keyed by seq.
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import type {} from "../src/realtime/latency";
import { API_URL, Stack, TOKEN } from "./stack";

const WS_P95_MS = 100; // AC-S7

test.describe.configure({ mode: "serial" });

let stack: Stack;

test.beforeAll(async () => {
  stack = await Stack.start();
});

test.afterAll(async () => {
  await stack?.stop();
});

test.beforeEach(async ({ context }) => {
  // What the token form stores; this is the e2e API's own token.
  await context.addInitScript((token) => window.localStorage.setItem("autora.operatorToken", token), TOKEN);
});

const auth = { Authorization: `Bearer ${TOKEN}` };
const rows = (page: Page) => page.locator('[data-testid^="event-"]');
const completedRows = (page: Page) => rows(page).filter({ hasText: "WORKFLOW_RUN_COMPLETED" });
const live = (page: Page) => page.locator('[role="status"][data-status="live"]');

async function openTimeline(page: Page): Promise<void> {
  await page.goto(`/admin/timeline?company=${stack.companyId}`);
  await expect(live(page)).toBeVisible({ timeout: 30_000 });
}

/** Seqs shown, top to bottom. */
function shownSeqs(page: Page): Promise<number[]> {
  return rows(page).evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-testid")!.slice("event-".length))));
}

interface ServerEvent {
  seq: number;
  event_type: string;
  workflow_run_id: string | null;
  run_id: string | null;
}

async function serverEvents(request: APIRequestContext): Promise<ServerEvent[]> {
  const all: ServerEvent[] = [];
  let after = 0;
  for (;;) {
    const res = await request.get(`${API_URL}/api/events`, {
      headers: auth,
      params: { company_id: stack.companyId, after, limit: 500 },
    });
    expect(res.ok()).toBe(true);
    const page = (await res.json()) as { items: ServerEvent[]; next_after: number; has_more: boolean };
    all.push(...page.items);
    if (!page.has_more) return all;
    after = page.next_after;
  }
}

async function startEcho(request: APIRequestContext, params: Record<string, unknown>): Promise<string> {
  const res = await request.post(`${API_URL}/api/companies/${stack.companyId}/workflows`, {
    headers: auth,
    data: { template: "echo.chain_v1", project_id: stack.projectId, params },
  });
  expect(res.status()).toBe(201);
  return ((await res.json()) as { id: string }).id;
}

/**
 * The page shows the server's events exactly: newest first, each once, and from the oldest one
 * shown up to now nothing missing (the buffer starts at the snapshot's last 100 events).
 */
async function expectSameAsServer(page: Page, request: APIRequestContext): Promise<number[]> {
  const compare = async () => {
    const shown = await shownSeqs(page);
    const oldest = Math.min(...shown);
    const expected = (await serverEvents(request))
      .map((e) => e.seq)
      .filter((seq) => seq >= oldest)
      .sort((x, y) => y - x);
    return { shown, expected };
  };
  // Let in-flight events land; the assertions below then report any difference in full.
  await expect
    .poll(async () => {
      const { shown, expected } = await compare();
      return JSON.stringify(shown) === JSON.stringify(expected);
    }, { timeout: 15_000 })
    .toBe(true)
    .catch(() => undefined);
  const { shown, expected } = await compare();
  expect(new Set(shown).size).toBe(shown.length); // no duplicates
  expect(shown).toEqual(expected); // newest first, no gaps
  return shown;
}

/** agent id -> activity state shown on its card. */
function cardStates(page: Page): Promise<Record<string, string>> {
  return page.locator('[data-testid^="agent-card-"]').evaluateAll((cards) =>
    Object.fromEntries(
      cards.map((card) => [
        card.getAttribute("data-testid")!.slice("agent-card-".length),
        card.querySelector("[data-state]")!.getAttribute("data-state")!,
      ]),
    ),
  );
}

const analystState = (page: Page) =>
  page.locator('[data-testid^="agent-card-"]', { hasText: "分析師" }).locator("[data-state]").getAttribute("data-state");

test("two tabs: the agent cards and the events change together, each event once", async ({ context, request }) => {
  const dashboards = [await context.newPage(), await context.newPage()];
  const timelines = [await context.newPage(), await context.newPage()];
  for (const page of dashboards) {
    await page.goto(`/admin/dashboard?company=${stack.companyId}`);
    await expect(live(page)).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('[data-testid^="agent-card-"]')).toHaveCount(3);
  }
  for (const page of timelines) await openTimeline(page);
  const before = await completedRows(timelines[0]).count();

  // The analyst's reply takes 4 s: long enough to see both tabs show it busy.
  const workflowId = await startEcho(request, { topic: "two tabs", pause: { task: "echo_analyze", attempt: 1, seconds: 4 } });

  await expect
    .poll(async () => `${await analystState(dashboards[0])}|${await analystState(dashboards[1])}`, { timeout: 30_000 })
    .toMatch(/^(THINKING|WORKING)\|\1$/);

  for (const page of timelines) {
    await expect(completedRows(page)).toHaveCount(before + 1, { timeout: 60_000 });
  }
  // Both dashboards end in the same state, the analyst no longer busy.
  await expect.poll(async () => JSON.stringify(await cardStates(dashboards[0])), { timeout: 10_000 }).toBe(
    JSON.stringify(await cardStates(dashboards[1])),
  );
  expect(await analystState(dashboards[0])).not.toMatch(/THINKING|WORKING/);

  const events = await serverEvents(request);
  expect(events.some((e) => e.event_type === "WORKFLOW_RUN_COMPLETED" && e.workflow_run_id === workflowId)).toBe(true);
  const seenA = await expectSameAsServer(timelines[0], request);
  const seenB = await expectSameAsServer(timelines[1], request);
  expect(seenB).toEqual(seenA);
  const ofWorkflow = events.filter((e) => e.workflow_run_id === workflowId).map((e) => e.seq);
  expect(ofWorkflow.length).toBeGreaterThan(20);
  for (const seq of ofWorkflow) expect(seenA).toContain(seq);

  // AC-S7: handling what arrives on the socket stays well under 100 ms (the soak test measures
  // the same number over hours; this is the same check on a real workflow's traffic)
  const ws = await dashboards[0].evaluate(() => window.__autoraRealtime?.stats() ?? null);
  expect(ws, "the page never recorded handling a message").not.toBeNull();
  expect(ws!.count).toBeGreaterThan(0);
  expect(ws!.p95).toBeLessThan(WS_P95_MS);
});

test("API down for 20 s: the page recovers by itself and misses nothing", async ({ page, request }) => {
  await openTimeline(page);
  const before = await completedRows(page).count();
  const writerDone = /\(writer, task echo_write attempt 1\): completed/;
  const writersBefore = stack.worker.count(writerDone);

  // The analyst's reply is slow, so the worker is still working when the API goes away.
  const workflowId = await startEcho(request, {
    topic: "api restart",
    pause: { task: "echo_analyze", attempt: 1, seconds: 8 },
  });
  await expect
    .poll(async () => (await serverEvents(request)).filter((e) => e.workflow_run_id === workflowId && e.event_type === "TASK_SUCCEEDED").length, {
      timeout: 30_000,
    })
    .toBe(1);
  const seenBeforeCrash = await shownSeqs(page);

  await stack.api.stop("SIGKILL");
  const downAt = Date.now();
  await expect(page.locator('[role="status"][data-status="reconnecting"], [role="status"][data-status="offline"]')).toBeVisible({
    timeout: 15_000,
  });

  // While the API is down the worker finishes the workflow; its events are only in the database.
  await stack.worker.waitFor(() => stack.worker.count(writerDone) > writersBefore, "the writer to finish", 60_000);
  expect(await completedRows(page).count()).toBe(before);
  await page.waitForTimeout(Math.max(0, 20_000 - (Date.now() - downAt)));

  await stack.startApi();
  // Recovers within 30 s of the API coming back, without a reload.
  await expect(live(page)).toBeVisible({ timeout: 30_000 });
  await expect(completedRows(page)).toHaveCount(before + 1, { timeout: 15_000 });

  const shown = await expectSameAsServer(page, request);
  const events = await serverEvents(request);
  expect(shown[0]).toBe(Math.max(...events.map((e) => e.seq))); // last seq == server head
  for (const seq of seenBeforeCrash) expect(shown).toContain(seq); // nothing lost
  const ofWorkflow = events.filter((e) => e.workflow_run_id === workflowId);
  for (const e of ofWorkflow) expect(shown).toContain(e.seq); // including what happened offline

  // The writer ran while the API was down: its trace has every one of its events.
  const writerRun = ofWorkflow.filter((e) => e.event_type === "AGENT_RUN_COMPLETED").at(-1)!.run_id!;
  const trace = await request.get(`${API_URL}/api/runs/${writerRun}/trace`, { headers: auth });
  const traceSeqs = ((await trace.json()) as { entries: { seq: number }[] }).entries.map((e) => e.seq);
  expect(traceSeqs.length).toBeGreaterThan(5);
  await page.goto(`/admin/trace/${writerRun}`);
  const traceRows = page.locator('[data-testid^="row-e"]');
  await expect(traceRows).toHaveCount(traceSeqs.length, { timeout: 15_000 });
  const traceShown = await traceRows.evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-testid")!.slice("row-e".length))));
  expect(traceShown).toEqual(traceSeqs);
});

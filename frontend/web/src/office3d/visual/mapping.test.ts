import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ACTIVITY_STATES, createRealtimeStore, type ActivityState, type AgentState } from "@/stores/realtime";

import { ROLE_LABEL, visualForAgent, visualState, type VisualState } from "./mapping";

const POSES = ["sit_idle", "sit_think", "sit_type", "sit_read", "stand", "walk", "slump"];
const SCREENS = ["off", "dim", "active", "alert"];
const LIGHTS = ["off", "on", "blink_amber", "blink_red"];
const TONES = ["muted", "info", "active", "warn", "error", "success"];
const ROLES = ["researcher", "analyst", "writer", "editor", "marketing", "ceo"];

function expectValid(v: VisualState) {
  expect(POSES).toContain(v.pose);
  expect(SCREENS).toContain(v.screen);
  expect(LIGHTS).toContain(v.deskLight);
  expect(TONES).toContain(v.badge.tone);
  expect(v.badge.text.length).toBeGreaterThan(0);
  if (v.bubble !== undefined) expect(v.bubble.length).toBeLessThanOrEqual(40);
}

const look = (v: VisualState) => [v.pose, v.screen, v.deskLight, v.badge.text, v.badge.tone, v.bubble ?? null];

describe("the 02 §7 table", () => {
  it.each([
    ["IDLE", {}, ["sit_idle", "dim", "off", "閒置", "muted", null]],
    ["THINKING", { phase: "plan" }, ["sit_think", "active", "on", "思考中", "info", "規劃中…"]],
    ["WORKING", { tool: "web_search" }, ["sit_type", "active", "on", "工作中", "active", "搜尋…"]],
    ["WAITING", { reason: "approval" }, ["sit_idle", "alert", "blink_amber", "等待審批", "warn", "等待審批…"]],
    ["WAITING", { reason: "upstream", waiting_on_roles: ["researcher"] }, ["sit_idle", "dim", "on", "等待研究員", "info", null]],
    ["WAITING", { reason: "upstream", waiting_on_roles: ["system"] }, ["sit_idle", "dim", "on", "等待系統", "info", null]],
    ["WAITING", { reason: "upstream", waiting_on_roles: ["human"] }, ["sit_idle", "dim", "on", "等待人工審批", "info", null]],
    ["WAITING", { reason: "budget" }, ["sit_idle", "alert", "blink_amber", "預算不足", "warn", null]],
    ["REVIEWING", { phase: "evaluate" }, ["sit_read", "active", "on", "檢查中", "info", "檢查輸出…"]],
    ["COMPLETED", { output_summary: "wrote note" }, ["stand", "active", "on", "已完成", "success", "wrote note"]],
    ["FAILED", { error_class: "BudgetExceeded" }, ["slump", "alert", "blink_red", "失敗", "error", "BudgetExceeded"]],
    ["PAUSED", { reason: "maintenance" }, ["sit_idle", "off", "off", "暫停", "muted", null]],
  ] as [ActivityState, Record<string, unknown>, unknown[]][])("%s %o", (state, detail, expected) => {
    expect(look(visualState({ state, detail, role: "researcher" }))).toEqual(expected);
  });

  it("role exceptions: the editor reads with read_* tools; everyone else types", () => {
    expect(visualState({ state: "WORKING", detail: { tool: "read_article" }, role: "editor" }).pose).toBe("sit_read");
    expect(visualState({ state: "WORKING", detail: { tool: "read_article" }, role: "writer" }).pose).toBe("sit_type");
    expect(visualState({ state: "WORKING", detail: { tool: "fetch_url" }, role: "editor" }).pose).toBe("sit_type");
    expect(look(visualState({ state: "THINKING", detail: {}, role: "ceo" })).slice(0, 2)).toEqual(["sit_think", "active"]);
  });

  it("progress: reported progress wins over the tool's, and shows as a count", () => {
    const detail = { tool: "fetch_url", progress: { label: "sources", current: 30, target: 40 } };
    expect(visualState({ state: "WORKING", detail, role: "researcher" }).bubble).toBe("讀取網頁… 30/40");
    const live = { label: "sources", current: 37, target: 40 };
    expect(visualState({ state: "WORKING", detail, role: "researcher", progress: live }).bubble).toBe("讀取網頁… 37/40");
    expect(visualState({ state: "WORKING", detail: { tool: "new_tool" }, role: "researcher" }).bubble).toBe("new_tool…");
  });

  it("a completion with a hand-off walks the result to the next role and back", () => {
    const handoff = [{ to_role: "analyst", task_id: "01a0b428-dc84-7340-9135-d2d934cbf8a8" }];
    expect(visualState({ state: "COMPLETED", detail: { handoff }, role: "researcher" }).walkTo).toEqual({
      targetRole: "analyst",
      taskId: handoff[0].task_id,
      carry: "document",
      returnAfter: true,
    });
    expect(visualState({ state: "COMPLETED", detail: { handoff: [] }, role: "writer" }).walkTo).toBeUndefined();
  });

  it("an aborted run says why; long text is clipped", () => {
    expect(visualState({ state: "FAILED", detail: { reason: "budget" }, role: "writer" }).bubble).toBe("預算用盡");
    const long = visualState({ state: "COMPLETED", detail: { output_summary: "x".repeat(200) }, role: "writer" });
    expect(long.bubble).toHaveLength(40);
    expect(long.bubble!.endsWith("…")).toBe(true);
  });

  it("every state x role x waiting reason maps to valid values", () => {
    for (const state of ACTIVITY_STATES) {
      for (const role of [...ROLES, "someone_new"]) {
        const details = state === "WAITING" ? ["approval", "budget", "upstream", "rate_limit", "other"].map((reason) => ({ reason })) : [{}];
        for (const detail of details) expectValid(visualState({ state, detail, role }));
      }
    }
    expect(Object.keys(ROLE_LABEL).sort()).toEqual([...ROLES].sort());
  });
});

describe("visualForAgent", () => {
  const agent = (activity: Partial<NonNullable<AgentState["activity"]>>, live: AgentState["liveProgress"] = null): AgentState => ({
    id: "a",
    role: "researcher",
    display_name: "Rae",
    avatar_key: "default",
    activity: { state: "IDLE", stored_state: "IDLE", detail: {}, since: "2026-09-19T00:00:00Z", run_id: "r1", task_id: null, last_event_seq: 1, ...activity },
    liveProgress: live,
  });

  it("COMPLETED past display_until reads IDLE (the projection's rule)", () => {
    const done = agent({ stored_state: "COMPLETED", state: "COMPLETED", detail: { display_until: "2026-09-19T00:00:20Z" } });
    expect(visualForAgent(done, new Date("2026-09-19T00:00:10Z"))!.badge.text).toBe("已完成");
    expect(visualForAgent(done, new Date("2026-09-19T00:00:21Z"))!.badge.text).toBe("閒置");
  });

  it("live progress only from the agent's current run", () => {
    const progress = { label: "sources", current: 5, target: 10 };
    const at = "2026-09-19T00:00:00Z";
    const working = { stored_state: "WORKING" as const, state: "WORKING" as const, detail: { tool: "fetch_url" } };
    expect(visualForAgent(agent(working, { runId: "r1", stepSeq: 1, tokensSoFar: 0, progress, at }), new Date())!.bubble).toBe("讀取網頁… 5/10");
    expect(visualForAgent(agent(working, { runId: "old", stepSeq: 1, tokensSoFar: 0, progress, at }), new Date())!.bubble).toBe("讀取網頁…");
  });

  it("an agent without activity yet has no visual state", () => {
    expect(visualForAgent({ ...agent({}), activity: null }, new Date())).toBeNull();
  });
});

describe("a real runtime history", () => {
  // Written by the Python contract test (make realtime-fixture): randomised real runtime runs.
  const fixture = JSON.parse(
    readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8"),
  ) as { snapshot_before: unknown; events: { occurred_at: string }[] };

  it("every agent maps to a valid visual state after every event", () => {
    const store = createRealtimeStore();
    store.getState().hydrate(fixture.snapshot_before);
    const seen = new Set<string>();
    const walks: string[] = [];
    for (const event of fixture.events) {
      expect(store.getState().applyEvent(event)).toBe(true);
      const now = new Date(event.occurred_at);
      const company = store.getState().company!;
      for (const a of Object.values(company.agents)) {
        const v = visualForAgent(a, now);
        if (!v) continue;
        expectValid(v);
        seen.add(v.badge.text);
        if (v.walkTo) walks.push(v.walkTo.targetRole);
      }
    }
    // the history exercises the main states, and hand-offs go to roles the company has
    for (const text of ["閒置", "思考中", "工作中", "檢查中", "已完成"]) expect(seen).toContain(text);
    const roles = new Set(Object.values(store.getState().company!.agents).map((a) => a.role));
    expect(walks.length).toBeGreaterThan(0);
    for (const role of walks) expect(roles).toContain(role);
  });
});

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentState, RealtimeState } from "@/realtime/reducer";
import type { ActivityState, TaskView } from "@/realtime/snapshot";

import { AgentCards } from "./AgentCards";
import { AgentPanelView, type PanelData } from "./AgentPanelView";
import {
  cardModel,
  nextSteps,
  producedCounts,
  toolStats,
  type Run,
  type Trace,
} from "./model";

afterEach(cleanup);

const NOW = new Date("2026-09-18T10:00:00Z");
const RUN = "01a0b3b0-0000-7000-8000-000000000001";
const TASK = "01a0b3b0-0000-7000-8000-0000000000aa";

function agent(state: ActivityState, detail: Record<string, unknown> = {}, extra: Partial<AgentState> = {}): AgentState {
  const runless = state === "IDLE" || state === "PAUSED";
  return {
    id: "agent-1",
    role: "researcher",
    display_name: "Rae",
    avatar_key: "default",
    department_id: null,
    office_zone_key: null,
    business_unit_key: null,
    department_key: null,
    activity: {
      state,
      stored_state: state,
      detail: runless ? detail : { run_id: RUN, task_id: TASK, task_name: "Research: EU AI Act", ...detail },
      since: "2026-09-18T09:58:30Z",
      run_id: runless ? null : RUN,
      task_id: runless ? null : TASK,
      last_event_seq: 10,
    },
    liveProgress: null,
    ...extra,
  };
}

function task(id: string, name: string, role: string, dependsOn: string[] = [], state = "PENDING"): TaskView {
  return {
    id, name, display_name: name, required_role: role, state, depends_on: dependsOn,
    workflow_run_id: null, attempt: 0, run_id: null, agent_id: null,
    since: "2026-09-18T09:00:00Z", last_event_seq: 1,
  };
}

function trace(entries: { event_type: string; payload: Record<string, unknown> }[]): Trace {
  return {
    run_id: RUN, company_id: "c", task_id: TASK, agent_id: "agent-1", attempt: 1, state: "RUNNING",
    cost_usd: "0", steps: [],
    entries: entries.map((e, i) => ({
      seq: i + 1, event_type: e.event_type, occurred_at: NOW.toISOString(),
      actor: { kind: "agent", id: "agent-1" }, payload: e.payload, step: null,
    })),
  } as Trace;
}

const evidence = (n: number) => Array.from({ length: n }, (_, i) => ({ type: "evidence", id: `e${i}` }));

describe("cards: the live layer", () => {
  it("shows task, tool and time for a working agent", () => {
    const card = cardModel(agent("WORKING", { tool: "fetch_url" }), NOW)!;
    expect(card).toMatchObject({
      name: "Rae", state: "WORKING", stateLabel: "工作中",
      taskName: "Research: EU AI Act", tool: "fetch_url", sinceMs: 90_000, progress: null,
    });
  });

  it("names why an agent waits, and an idle agent has no task", () => {
    expect(cardModel(agent("WAITING", { reason: "approval" }), NOW)!.stateLabel).toBe("等待審批");
    expect(cardModel(agent("WAITING", { reason: "upstream" }), NOW)!.stateLabel).toBe("等待上游");
    expect(cardModel(agent("IDLE"), NOW)).toMatchObject({ taskName: null, tool: null });
  });

  it("shows progress only when the runtime reports it", () => {
    expect(cardModel(agent("WORKING", { tool: "fetch_url" }), NOW)!.progress).toBeNull();
    const reported = { label: "sources", current: 37, target: 40 };
    expect(cardModel(agent("WORKING", { progress: reported }), NOW)!.progress).toEqual(reported);
    const live = agent("WORKING", {}, {
      liveProgress: { runId: RUN, stepSeq: 3, tokensSoFar: 900, progress: reported, at: NOW.toISOString() },
    });
    expect(cardModel(live, NOW)).toMatchObject({ progress: reported, tokens: 900 });
    const stale = agent("WORKING", {}, {
      liveProgress: { runId: "another-run", stepSeq: 3, tokensSoFar: 900, progress: reported, at: NOW.toISOString() },
    });
    expect(cardModel(stale, NOW)).toMatchObject({ progress: null, tokens: null });
  });
});

describe("panel: next step, produced, tools", () => {
  const company = (tasks: TaskView[]): RealtimeState => ({
    companyId: "c", lastSeq: 10, agents: {}, recentEvents: [], cycle: null,
    tasks: Object.fromEntries(tasks.map((t) => [t.id, t])),
  });

  it("next step = the tasks that depend on the current one (workflow downstream)", () => {
    const state = company([
      task(TASK, "Research", "researcher", [], "RUNNING"),
      task("t2", "Analyse", "analyst", [TASK]),
      task("t3", "Write", "writer", ["t2"]),
    ]);
    expect(nextSteps(agent("WORKING"), state)).toEqual([
      { taskId: "t2", name: "Analyse", role: "analyst", state: "PENDING" },
    ]);
  });

  it("after completion, the recorded handoff", () => {
    const state = company([task("t2", "Analyse", "analyst", [TASK], "READY")]);
    const done = agent("COMPLETED", { handoff: [{ to_role: "analyst", task_id: "t2" }] });
    expect(nextSteps(done, state)).toEqual([{ taskId: "t2", name: "Analyse", role: "analyst", state: "READY" }]);
    expect(nextSteps(agent("IDLE"), state)).toEqual([]);
  });

  it("Sources 37 is the number of evidence artifacts the run's tools produced", () => {
    const t = trace([
      { event_type: "TOOL_CALLED", payload: { tool: "fetch_url" } },
      { event_type: "TOOL_COMPLETED", payload: { tool: "fetch_url", duration_ms: 400, produced: evidence(30) } },
      { event_type: "TOOL_CALLED", payload: { tool: "fetch_url" } },
      { event_type: "TOOL_COMPLETED", payload: { tool: "fetch_url", duration_ms: 600, produced: evidence(7) } },
      { event_type: "TOOL_CALLED", payload: { tool: "web_search" } },
      { event_type: "TOOL_FAILED", payload: { tool: "web_search", error_class: "Timeout" } },
      { event_type: "AGENT_THINKING", payload: { phase: "reason" } },
    ]);
    expect(producedCounts(t)).toEqual([{ type: "evidence", label: "來源", count: 37 }]);
    expect(toolStats(t)).toEqual([
      { tool: "fetch_url", calls: 2, ok: 2, failed: 0, avgMs: 500 },
      { tool: "web_search", calls: 1, ok: 0, failed: 1, avgMs: null },
    ]);
    expect(producedCounts(undefined)).toEqual([]);
  });
});

describe("views", () => {
  it("cards select an agent", () => {
    const onSelect = vi.fn();
    const card = cardModel(agent("WORKING", { tool: "fetch_url" }), NOW)!;
    render(<AgentCards cards={[card]} selectedId={null} onSelect={onSelect} />);
    const button = screen.getByTestId("agent-card-agent-1");
    expect(within(button).getByText("工作中")).toBeTruthy();
    expect(within(button).getByText("工具：fetch_url")).toBeTruthy();
    expect(within(button).queryByTestId("progress")).toBeNull();
    fireEvent.click(button);
    expect(onSelect).toHaveBeenCalledWith("agent-1");
  });

  function panel(overrides: Partial<PanelData> = {}): PanelData {
    const t = trace([
      { event_type: "TOOL_CALLED", payload: { tool: "fetch_url" } },
      { event_type: "TOOL_COMPLETED", payload: { tool: "fetch_url", duration_ms: 400, produced: evidence(37) } },
    ]);
    return {
      card: cardModel(agent("WORKING", { tool: "fetch_url" }), NOW)!,
      runId: RUN,
      nextSteps: [{ taskId: "t2", name: "Analyse", role: "analyst", state: "PENDING" }],
      links: [{ label: "Story", href: "/newsroom/stories/s1" }],
      run: { id: RUN, attempt: 1, steps_count: 5, cost_usd: "0.0123", output: { note_id: "n1" }, evaluation: null, error: null } as unknown as Run,
      trace: { ...t, steps: [{ seq: 0, kind: "think", summary: "Plan the search", cost_usd: "0.001", has_blob: true, tool_calls: null, created_at: NOW.toISOString() }] } as Trace,
      produced: producedCounts(t),
      tools: toolStats(t),
      loading: false,
      error: null,
      ...overrides,
    };
  }

  it("the live tab shows the AC-5 fields from real data", () => {
    render(<AgentPanelView data={panel()} tab="live" onTab={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Rae 的詳細資訊" });
    expect(within(dialog).getByText("Research: EU AI Act")).toBeTruthy();
    expect(within(dialog).getByText("fetch_url")).toBeTruthy();
    expect(within(dialog).getByTestId("produced-evidence").textContent).toBe("來源 37");
    expect(within(dialog).getByText("Analyse（分析師）")).toBeTruthy();
    expect(within(dialog).getByRole("link", { name: "Story" }).getAttribute("href")).toBe("/newsroom/stories/s1");
    expect(within(dialog).queryByTestId("progress")).toBeNull(); // not reported: no bar
    expect(within(dialog).getByText(/第 1 次嘗試・5 步/)).toBeTruthy();
  });

  it("steps, tools and output tabs; tab switching and close", () => {
    const onTab = vi.fn();
    const onClose = vi.fn();
    const { rerender } = render(<AgentPanelView data={panel()} tab="steps" onTab={onTab} onClose={onClose} />);
    expect(screen.getByText("Plan the search")).toBeTruthy();
    rerender(<AgentPanelView data={panel()} tab="tools" onTab={onTab} onClose={onClose} />);
    expect(screen.getByRole("cell", { name: "fetch_url" })).toBeTruthy();
    rerender(<AgentPanelView data={panel()} tab="output" onTab={onTab} onClose={onClose} />);
    expect(screen.getByText(/"note_id": "n1"/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "工具" }));
    expect(onTab).toHaveBeenCalledWith("tools");
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("an idle agent without a run, and a detail error", () => {
    const idle = panel({
      card: cardModel(agent("IDLE"), NOW)!, runId: null, run: undefined, trace: undefined,
      produced: [], tools: [], nextSteps: [], links: [], error: "404 Not Found",
    });
    render(<AgentPanelView data={idle} tab="live" onTab={() => {}} onClose={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("404 Not Found");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

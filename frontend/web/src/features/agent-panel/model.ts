// Agent cards and the agent detail panel (T-310, 3d-office/01 §3.7): what to show, from real
// data only.
//   live layer   : realtime store  -> state, task, tool, since, live progress, next step
//   detail layer : REST (run, trace) -> steps, tool statistics, produced counts, output
// Progress is shown only when the runtime reports it (a tool or the task); otherwise the
// elapsed time. Nothing here estimates a percentage.
import type { Schemas } from "@/api/client";
import { effectiveState, type AgentState, type RealtimeState } from "@/realtime/reducer";
import type { ActivityState } from "@/realtime/snapshot";
import { withCompany } from "@/features/company/CompanyScope";

export type Trace = Schemas["Trace"];
export type Run = Schemas["RunOut"];

export const STATE_LABEL: Record<ActivityState, string> = {
  IDLE: "閒置",
  THINKING: "思考中",
  WORKING: "工作中",
  WAITING: "等待中",
  REVIEWING: "檢查中",
  COMPLETED: "已完成",
  FAILED: "失敗",
  PAUSED: "暫停",
};

const WAIT_REASON: Record<string, string> = {
  approval: "等待審批",
  upstream: "等待上游",
  budget: "等待預算",
  rate_limit: "等待限流解除",
};

/** Names for produced artifact types; unknown types show as they are. */
export const PRODUCED_LABEL: Record<string, string> = {
  evidence: "來源",
  claim: "論點",
  article_version: "稿件版本",
  echo_note: "筆記",
};

export const ROLE_LABEL: Record<string, string> = {
  researcher: "研究員",
  analyst: "分析師",
  writer: "寫手",
  editor: "編輯",
  marketing: "行銷",
  ceo: "執行長",
  finance: "財務長",
  business: "商業開發",
  human: "人工審批",
  system: "系統",
};

export interface Progress {
  label: string;
  current: number;
  target: number | null;
}

export interface CardModel {
  id: string;
  name: string;
  role: string;
  state: ActivityState;
  stateLabel: string;
  taskName: string | null;
  tool: string | null;
  sinceMs: number;
  progress: Progress | null;
  tokens: number | null;
}

function detailString(detail: Record<string, unknown>, key: string): string | null {
  const value = detail[key];
  return typeof value === "string" && value ? value : null;
}

function reportedProgress(agent: AgentState, state: ActivityState): Progress | null {
  const detail = agent.activity?.detail ?? {};
  const fromActivity = detail.progress as Progress | null | undefined;
  const live = agent.liveProgress?.runId === agent.activity?.run_id ? agent.liveProgress : null;
  if (!["THINKING", "WORKING", "REVIEWING"].includes(state)) return null;
  return live?.progress ?? fromActivity ?? null;
}

export function cardModel(agent: AgentState, now: Date): CardModel | null {
  const activity = agent.activity;
  if (!activity) return null;
  const state = effectiveState(activity, now);
  const detail = activity.detail;
  const busy = state === "THINKING" || state === "WORKING" || state === "REVIEWING";
  const waitReason = state === "WAITING" ? WAIT_REASON[detailString(detail, "reason") ?? ""] : null;
  const live = agent.liveProgress?.runId === activity.run_id ? agent.liveProgress : null;
  return {
    id: agent.id,
    name: agent.display_name,
    role: agent.role,
    state,
    stateLabel: waitReason ?? STATE_LABEL[state],
    taskName: state === "IDLE" ? null : detailString(detail, "task_name"),
    tool: state === "WORKING" ? detailString(detail, "tool") : null,
    sinceMs: now.getTime() - Date.parse(activity.since),
    progress: reportedProgress(agent, state),
    tokens: busy && live ? live.tokensSoFar : null,
  };
}

export interface NextStep {
  taskId: string;
  name: string;
  role: string;
  state: string;
}

/**
 * Who works next: the handoff the runtime recorded when the run completed, otherwise the tasks
 * that depend on the agent's current task (the workflow template's downstream nodes). Never
 * guessed.
 */
export function nextSteps(agent: AgentState, company: RealtimeState): NextStep[] {
  const detail = agent.activity?.detail ?? {};
  const handoff = Array.isArray(detail.handoff) ? (detail.handoff as { task_id: string; to_role: string }[]) : null;
  if (handoff && handoff.length) {
    return handoff.map((h) => {
      const task = company.tasks[h.task_id];
      return { taskId: h.task_id, name: task?.display_name ?? h.task_id, role: h.to_role, state: task?.state ?? "" };
    });
  }
  const taskId = agent.activity?.task_id;
  if (!taskId) return [];
  return Object.values(company.tasks)
    .filter((task) => task.depends_on.includes(taskId))
    .map((task) => ({ taskId: task.id, name: task.display_name, role: task.required_role, state: task.state }));
}

/** Artifacts the run produced, by type, counted from real TOOL_COMPLETED events. */
export function producedCounts(trace: Trace | undefined): { type: string; label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const entry of trace?.entries ?? []) {
    if (entry.event_type !== "TOOL_COMPLETED") continue;
    const produced = (entry.payload as { produced?: { type: string }[] }).produced ?? [];
    for (const ref of produced) counts.set(ref.type, (counts.get(ref.type) ?? 0) + 1);
  }
  return [...counts].map(([type, count]) => ({ type, label: PRODUCED_LABEL[type] ?? type, count }));
}

export interface ToolStats {
  tool: string;
  calls: number;
  ok: number;
  failed: number;
  avgMs: number | null;
}

/** Per tool: calls, successes, failures, average duration (from TOOL_* events). */
export function toolStats(trace: Trace | undefined): ToolStats[] {
  const stats = new Map<string, { calls: number; ok: number; failed: number; totalMs: number }>();
  const get = (tool: string) => {
    let row = stats.get(tool);
    if (!row) stats.set(tool, (row = { calls: 0, ok: 0, failed: 0, totalMs: 0 }));
    return row;
  };
  for (const entry of trace?.entries ?? []) {
    const payload = entry.payload as { tool?: string; duration_ms?: number };
    if (!payload.tool) continue;
    if (entry.event_type === "TOOL_CALLED") get(payload.tool).calls += 1;
    else if (entry.event_type === "TOOL_COMPLETED") {
      const row = get(payload.tool);
      row.ok += 1;
      row.totalMs += payload.duration_ms ?? 0;
    } else if (entry.event_type === "TOOL_FAILED") get(payload.tool).failed += 1;
  }
  return [...stats].map(([tool, row]) => ({
    tool,
    calls: row.calls,
    ok: row.ok,
    failed: row.failed,
    avgMs: row.ok ? Math.round(row.totalMs / row.ok) : null,
  }));
}

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小時 ${minutes % 60} 分`;
}


/** The links an agent's activity names, for the operator to click. The backend names a page, not
 * a company, and a page opened without one shows the default company — so the company the panel
 * belongs to is added (D-041). Anything that is not a list of {label, href} is no links at all. */
export function panelLinks(raw: unknown, companyId: string): { label: string; href: string }[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((l): l is { label: string; href: string } => typeof l?.label === "string" && typeof l?.href === "string")
    .map((link) => ({ label: link.label, href: withCompany(link.href, companyId) }));
}

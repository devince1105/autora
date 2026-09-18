// Trace viewer (T-311, 3d-office/01 §3.8): every row is a real event or a real step of the run;
// nothing is synthesised on the client. Known event types get a readable line; any other type
// (newer server, other domain) is still shown, with its raw payload.
import type { Schemas } from "@/api/client";

export type Trace = Schemas["Trace"];
export type TraceEntry = Schemas["TraceEntry"];
export type Step = Schemas["StepView"];

export type Tone = "neutral" | "think" | "work" | "review" | "ok" | "warn" | "danger";

export interface Row {
  key: string;
  kind: "event" | "step";
  seq: number | null;
  at: string;
  label: string;
  summary: string | null;
  tone: Tone;
  known: boolean;
  payload: Record<string, unknown> | null;
  step: Step | null;
}

type Payload = Record<string, unknown>;
const s = (value: unknown) => (typeof value === "string" && value ? value : null);
const n = (value: unknown) => (typeof value === "number" ? value : null);

/** Readable line per event type: [label, tone, summary]. */
const FORMAT: Record<string, (p: Payload) => [string, Tone, string | null]> = {
  TASK_CREATED: (p) => ["任務建立", "neutral", s(p.display_name)],
  TASK_READY: () => ["任務就緒", "neutral", null],
  TASK_STARTED: (p) => ["任務開始", "neutral", `第 ${n(p.attempt) ?? "?"} 次嘗試`],
  TASK_WAITING: (p) => ["任務等待", "warn", s(p.reason) === "approval" ? "等待審批" : s(p.reason)],
  TASK_SUCCEEDED: (p) => {
    const unlocks = Array.isArray(p.unlocks) ? p.unlocks.length : 0;
    return ["任務成功", "ok", [s(p.output_ref), unlocks ? `解鎖 ${unlocks} 個下游任務` : null].filter(Boolean).join("・") || null];
  },
  TASK_FAILED: (p) => [p.final ? "任務失敗" : "任務失敗（將重試）", p.final ? "danger" : "warn", s(p.error_class)],
  TASK_CANCELLED: (p) => ["任務取消", "warn", s(p.reason)],
  TASK_BLOCKED: () => ["任務受阻", "warn", "預算不足"],
  AGENT_RUN_STARTED: (p) => ["執行開始", "neutral", `第 ${n(p.attempt) ?? "?"} 次嘗試・${s(p.task_name) ?? ""}`],
  AGENT_THINKING: (p) => ["思考", "think", s(p.phase) === "plan" ? "規劃" : s(p.phase) === "reason" ? "推理" : s(p.phase)],
  AGENT_WORKING: (p) => ["使用工具", "work", s(p.tool)],
  AGENT_REVIEWING: (p) => [
    s(p.phase) === "repair" ? "修補" : "檢查",
    "review",
    n(p.issues_count) ? `${p.issues_count} 個問題` : "沒有問題",
  ],
  AGENT_WAITING: (p) => ["等待", "warn", s(p.reason)],
  AGENT_RUN_COMPLETED: (p) => ["執行完成", "ok", [s(p.output_summary), p.cost_usd ? `US$${Number(p.cost_usd).toFixed(4)}` : null].filter(Boolean).join("・") || null],
  AGENT_RUN_FAILED: (p) => [p.final ? "執行失敗" : "執行失敗（將重試）", "danger", [s(p.error_class), s(p.message)].filter(Boolean).join("：") || null],
  AGENT_RUN_ABORTED: (p) => ["執行中止", "danger", [s(p.reason), s(p.message)].filter(Boolean).join("：") || null],
  AGENT_IDLE: () => ["回到閒置", "neutral", null],
  TOOL_CALLED: (p) => ["呼叫工具", "work", [s(p.tool), s(p.args_summary)].filter(Boolean).join(" ") || null],
  TOOL_COMPLETED: (p) => {
    const produced = Array.isArray(p.produced) ? p.produced.length : 0;
    return [
      "工具完成",
      "ok",
      [s(p.tool), n(p.duration_ms) !== null ? `${p.duration_ms} ms` : null, s(p.result_summary), produced ? `產出 ${produced}` : null]
        .filter(Boolean)
        .join("・") || null,
    ];
  },
  TOOL_FAILED: (p) => ["工具失敗", "danger", [s(p.tool), s(p.error_class), s(p.message)].filter(Boolean).join("・") || null],
  POLICY_DENIED: (p) => ["政策拒絕", "danger", [s(p.action), s(p.detail)].filter(Boolean).join("：") || null],
  APPROVAL_REQUESTED: (p) => ["請求審批", "warn", s(p.summary)],
  APPROVAL_APPROVED: () => ["審批通過", "ok", null],
  APPROVAL_REJECTED: (p) => ["審批駁回", "danger", s(p.reason)],
  APPROVAL_EXPIRED: () => ["審批過期", "warn", null],
  BUDGET_EXHAUSTED: (p) => ["預算用盡", "danger", s(p.scope)],
};

export function eventRow(entry: TraceEntry): Row {
  const payload = (entry.payload ?? {}) as Payload;
  const format = FORMAT[entry.event_type];
  const [label, tone, summary] = format ? format(payload) : [entry.event_type, "neutral" as Tone, null];
  return {
    key: `e${entry.seq}`,
    kind: "event",
    seq: entry.seq,
    at: entry.occurred_at,
    label,
    summary,
    tone,
    known: Boolean(format),
    payload,
    step: entry.step ?? null,
  };
}

function stepRow(step: Step): Row {
  return {
    key: `s${step.seq}`,
    kind: "step",
    seq: null,
    at: step.created_at,
    label: `步驟 #${step.seq}（${step.kind}）`,
    summary: step.summary,
    tone: "neutral",
    known: true,
    payload: null,
    step,
  };
}

/**
 * Events in seq order; a step no event refers to (e.g. an evaluate step) is placed by time.
 * The trace endpoint already joined each event to its step (payload.step_seq).
 */
export function traceRows(trace: Trace | undefined): Row[] {
  if (!trace) return [];
  const rows = trace.entries.map(eventRow);
  // Several events share one step (WORKING, TOOL_CALLED, TOOL_COMPLETED): show it once, under
  // the first of them.
  const shown = new Set<number>();
  for (const row of rows) {
    if (!row.step) continue;
    if (shown.has(row.step.seq)) row.step = null;
    else shown.add(row.step.seq);
  }
  const referenced = shown;
  const orphans = trace.steps.filter((step) => !referenced.has(step.seq)).map(stepRow);
  for (const orphan of orphans) {
    const at = Date.parse(orphan.at);
    const index = rows.findIndex((row) => Date.parse(row.at) > at);
    rows.splice(index === -1 ? rows.length : index, 0, orphan);
  }
  return rows;
}

export interface TraceSummary {
  events: number;
  steps: number;
  toolCalls: number;
  failures: number;
  unknownTypes: string[];
}

export function traceSummary(trace: Trace | undefined, rows: Row[]): TraceSummary {
  return {
    events: trace?.entries.length ?? 0,
    steps: trace?.steps.length ?? 0,
    toolCalls: rows.filter((r) => r.kind === "event" && r.label === "呼叫工具").length,
    failures: rows.filter((r) => r.tone === "danger").length,
    unknownTypes: [...new Set(rows.filter((r) => !r.known).map((r) => r.label))],
  };
}

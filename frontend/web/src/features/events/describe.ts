// How an event reads to an operator (T-311, T-312): a short zh-TW label, a tone for its dot and
// a one-line summary from the payload. Shared by the trace viewer and the event timeline, so an
// event reads the same everywhere. Unknown types are not dropped: the caller shows the type and
// the raw payload.
export type Tone = "neutral" | "think" | "work" | "review" | "ok" | "warn" | "danger";

/** Dot colour per tone (Tailwind tokens). */
export const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-neutral",
  think: "bg-accent",
  work: "bg-ok",
  review: "bg-accent",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
};

type Payload = Record<string, unknown>;
const s = (value: unknown) => (typeof value === "string" && value ? value : null);
const n = (value: unknown) => (typeof value === "number" ? value : null);
const duration = (value: unknown) => (n(value) === null ? null : `${Math.round((value as number) / 1000)} 秒`);
const money = (p: Payload) => (s(p.amount) ? `${s(p.currency) ?? "USD"} ${p.amount}` : null);

/** Readable line per event type: [label, tone, summary]. Anything else is shown as its type. */
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
  WORKFLOW_RUN_CREATED: (p) => ["工作流程建立", "neutral", [s(p.template), Array.isArray(p.task_ids) ? `${p.task_ids.length} 個任務` : null].filter(Boolean).join("・") || null],
  WORKFLOW_RUN_COMPLETED: (p) => ["工作流程完成", "ok", duration(p.duration_ms)],
  WORKFLOW_RUN_FAILED: (p) => ["工作流程失敗", "danger", [s(p.reason), duration(p.duration_ms)].filter(Boolean).join("・") || null],
  WORKFLOW_RUN_CANCELLED: (p) => ["工作流程取消", "warn", s(p.reason)],
  AGENT_CREATED: (p) => ["新代理", "neutral", [s(p.display_name), s(p.role)].filter(Boolean).join("・") || null],
  AGENT_PAUSED: (p) => ["代理暫停", "warn", s(p.reason)],
  AGENT_RESUMED: (p) => ["代理恢復", "neutral", s(p.reason)],
  TOOL_DENIED: (p) => [p.decision === "NEEDS_APPROVAL" ? "工具待審批" : "工具被拒", p.decision === "NEEDS_APPROVAL" ? "warn" : "danger", [s(p.tool), s(p.rule_id)].filter(Boolean).join("・") || null],
  EXPENSE_RECORDED: (p) => ["支出", "neutral", [s(p.category), money(p)].filter(Boolean).join("・") || null],
  REVENUE_RECORDED: (p) => ["營收", "ok", [s(p.category), money(p)].filter(Boolean).join("・") || null],
  BUDGET_ALLOCATED: (p) => ["預算配置", "neutral", [s(p.period), money(p)].filter(Boolean).join("・") || null],
  SCHEDULE_FIRED: (p) => ["排程觸發", "neutral", s(p.schedule_name)],
  COMPANY_CREATED: (p) => ["公司建立", "neutral", s(p.name)],
  PROJECT_APPROVED: (p) => ["專案核准", "ok", s(p.name)],
  BUDGET_EXHAUSTED: (p) => ["預算用盡", "danger", s(p.scope)],
  // newsroom (T-501)
  SOURCE_POLLED: (p) =>
    s(p.error)
      ? ["來源讀取失敗", "warn", s(p.error)]
      : ["讀取來源", "neutral", `新增 ${n(p.count) ?? 0} 則・共 ${n(p.seen) ?? 0} 則`],
  SOURCE_ITEM_DISCOVERED: (p) => ["新來源項目", "neutral", s(p.title)],
  SOURCE_PAUSED: (p) => ["來源暫停", "danger", s(p.reason)],
};

export interface Described {
  label: string;
  tone: Tone;
  summary: string | null;
  known: boolean;
}

export function describeEvent(eventType: string, payload: unknown): Described {
  const format = FORMAT[eventType];
  if (!format) return { label: eventType, tone: "neutral", summary: null, known: false };
  const [label, tone, summary] = format((payload ?? {}) as Payload);
  return { label, tone, summary, known: true };
}

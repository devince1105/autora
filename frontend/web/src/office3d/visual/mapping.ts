// Activity -> what the office shows (T-403, 3d-office/02 §7). The only place that knows how a
// state looks: pose, screen, desk light, badge, bubble, and the hand-off walk after a
// completion. Pure, never stored, never sent back; the 3D scene and the 2D board both use it.
import {
  effectiveState,
  type ActivityState,
  type AgentState,
} from "@/stores/realtime";

export type Pose = "sit_idle" | "sit_think" | "sit_type" | "sit_read" | "stand" | "walk" | "slump";
export type Screen = "off" | "dim" | "active" | "alert";
export type DeskLight = "off" | "on" | "blink_amber" | "blink_red";
export type BadgeTone = "muted" | "info" | "active" | "warn" | "error" | "success";

export interface Progress {
  label: string;
  current: number;
  target: number | null;
}

export interface VisualState {
  pose: Pose;
  screen: Screen;
  deskLight: DeskLight;
  badge: { text: string; tone: BadgeTone };
  bubble?: string;
  /**
   * After a completion with a hand-off: carry the result to the next role's desk and come back.
   * The Courier (T-408) resolves the role to a desk; the walk itself is a cue (T-407), not a state.
   */
  walkTo?: { targetRole: string; taskId: string; carry: "document" | "none"; returnAfter: boolean };
}

export interface MappingInput {
  state: ActivityState;
  detail: Record<string, unknown>;
  role: string;
  /** Reported progress of the current run (tool or ephemeral), if any. */
  progress?: Progress | null;
}

export const ROLE_LABEL: Record<string, string> = {
  researcher: "研究員",
  analyst: "分析師",
  writer: "寫手",
  editor: "編輯",
  marketing: "行銷",
  ceo: "執行長",
  finance: "財務長",
};

/** Workflow steps no agent runs (T-514) that an agent can be waiting on. */
const STEP_LABEL: Record<string, string> = { human: "人工審批", system: "系統" };

const TOOL_LABEL: Record<string, string> = {
  web_search: "搜尋",
  fetch_url: "讀取網頁",
  extract_evidence: "擷取證據",
  echo_note: "寫筆記",
};

const THINK_PHASE: Record<string, string> = { plan: "規劃中…", reason: "推理中…", finalize: "整理結果…" };
const ABORT_REASON: Record<string, string> = { budget: "預算用盡", policy: "政策拒絕", timeout: "逾時", human: "人工中止" };

const BUBBLE_MAX = 40;

const str = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

function clip(text: string | null | undefined): string | undefined {
  if (!text) return undefined;
  return text.length > BUBBLE_MAX ? `${text.slice(0, BUBBLE_MAX - 1)}…` : text;
}

function progressText(progress: Progress | null | undefined): string | null {
  if (!progress) return null;
  return progress.target ? `${progress.current}/${progress.target}` : `${progress.current}`;
}

function detailProgress(detail: Record<string, unknown>): Progress | null {
  const p = detail.progress as Partial<Progress> | null | undefined;
  return p && typeof p.current === "number" && typeof p.label === "string"
    ? { label: p.label, current: p.current, target: typeof p.target === "number" ? p.target : null }
    : null;
}

function waiting(detail: Record<string, unknown>): VisualState {
  const reason = str(detail.reason);
  if (reason === "approval") {
    return { pose: "sit_idle", screen: "alert", deskLight: "blink_amber", badge: { text: "等待審批", tone: "warn" }, bubble: "等待審批…" };
  }
  if (reason === "budget") {
    return { pose: "sit_idle", screen: "alert", deskLight: "blink_amber", badge: { text: "預算不足", tone: "warn" } };
  }
  if (reason === "upstream") {
    const roles = Array.isArray(detail.waiting_on_roles) ? (detail.waiting_on_roles as unknown[]).filter((r) => typeof r === "string") : [];
    const who = roles.map((r) => ROLE_LABEL[r as string] ?? STEP_LABEL[r as string] ?? (r as string)).join("、");
    return { pose: "sit_idle", screen: "dim", deskLight: "on", badge: { text: who ? `等待${who}` : "等待上游", tone: "info" } };
  }
  if (reason === "rate_limit") {
    return { pose: "sit_idle", screen: "dim", deskLight: "on", badge: { text: "等待限流解除", tone: "info" } };
  }
  return { pose: "sit_idle", screen: "dim", deskLight: "on", badge: { text: "等待中", tone: "info" } };
}

export function visualState({ state, detail, role, progress }: MappingInput): VisualState {
  switch (state) {
    case "IDLE":
      return { pose: "sit_idle", screen: "dim", deskLight: "off", badge: { text: "閒置", tone: "muted" } };
    case "THINKING": {
      // The CEO exception in 02 §7 (sit_think, screen active) is what every role gets.
      const phase = str(detail.phase);
      return {
        pose: "sit_think",
        screen: "active",
        deskLight: "on",
        badge: { text: "思考中", tone: "info" },
        bubble: clip(phase ? (THINK_PHASE[phase] ?? phase) : null),
      };
    }
    case "WORKING": {
      const tool = str(detail.tool);
      const reading = role === "editor" && tool?.startsWith("read_");
      const label = tool ? (TOOL_LABEL[tool] ?? tool) : null;
      const count = progressText(progress ?? detailProgress(detail));
      return {
        pose: reading ? "sit_read" : "sit_type",
        screen: "active",
        deskLight: "on",
        badge: { text: "工作中", tone: "active" },
        bubble: clip([label ? `${label}…` : null, count].filter(Boolean).join(" ") || null),
      };
    }
    case "WAITING":
      return waiting(detail);
    case "REVIEWING":
      return {
        pose: "sit_read",
        screen: "active",
        deskLight: "on",
        badge: { text: "檢查中", tone: "info" },
        bubble: str(detail.phase) === "repair" ? "修正輸出…" : "檢查輸出…",
      };
    case "COMPLETED": {
      const handoff = Array.isArray(detail.handoff) ? (detail.handoff as { to_role?: unknown; task_id?: unknown }[]) : [];
      const next = handoff.find((h) => typeof h.to_role === "string" && typeof h.task_id === "string");
      return {
        pose: "stand",
        screen: "active",
        deskLight: "on",
        badge: { text: "已完成", tone: "success" },
        bubble: clip(str(detail.output_summary) ?? str(detail.summary)),
        ...(next
          ? { walkTo: { targetRole: next.to_role as string, taskId: next.task_id as string, carry: "document" as const, returnAfter: true } }
          : {}),
      };
    }
    case "FAILED": {
      const reason = str(detail.reason);
      return {
        pose: "slump",
        screen: "alert",
        deskLight: "blink_red",
        badge: { text: "失敗", tone: "error" },
        bubble: clip(str(detail.error_class) ?? (reason ? (ABORT_REASON[reason] ?? reason) : null)),
      };
    }
    case "PAUSED":
      return { pose: "sit_idle", screen: "off", deskLight: "off", badge: { text: "暫停", tone: "muted" } };
    default: {
      const unreachable: never = state;
      throw new Error(`no visual state for ${String(unreachable)}`);
    }
  }
}

/**
 * The visual state of an agent now: the projection's effective state (COMPLETED past
 * display_until reads IDLE) and the live progress of its current run only.
 */
export function visualForAgent(agent: AgentState, now: Date): VisualState | null {
  const activity = agent.activity;
  if (!activity) return null;
  const live = agent.liveProgress;
  const progress = live && live.runId === activity.run_id ? live.progress : null;
  return visualState({ state: effectiveState(activity, now), detail: activity.detail, role: agent.role, progress });
}

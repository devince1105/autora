// Approval inbox (T-313): the operator's list of decisions the runtime is waiting for. The list
// is server state (GET /api/approvals); deciding is a REST command, and the list is refreshed by
// the APPROVAL_* event it causes (api/invalidation.ts), not by editing the cache.
import type { Schemas } from "@/api/client";
import { formatDuration } from "@/features/agent-panel/model";
import type { AgentState } from "@/realtime/reducer";

export type Approval = Schemas["ApprovalOut"];
export type ApprovalState = "PENDING" | "APPROVED" | "REJECTED" | "RETURNED" | "EXPIRED";
/** ``revise``: send it back with what to change (D-044). */
export type Decision = "approve" | "reject" | "revise";

export const STATES: { id: ApprovalState; label: string }[] = [
  { id: "PENDING", label: "待審批" },
  { id: "APPROVED", label: "已核准" },
  { id: "RETURNED", label: "已退回修改" },
  { id: "REJECTED", label: "已駁回" },
  { id: "EXPIRED", label: "已過期" },
];

/**
 * What to call each kind of decision. An open map on purpose: the backend stores the kind as a
 * token and a domain names its own (§9), so a kind this page has never seen shows its token
 * rather than nothing — "article" is the newsroom's word, kept here only as a translation.
 */
const KIND_LABEL: Record<string, string> = {
  tool_call: "工具呼叫",
  command: "指令",
  project: "專案",
  kill: "終止專案",
  strategy: "策略",
  article: "文章",
};

const ACTOR_KIND: Record<string, string> = { system: "系統", human: "人員", agent: "代理" };

export interface ApprovalCard {
  id: string;
  state: string;
  kind: string;
  action: string | null;
  summary: string;
  requester: string;
  /** What will run once approved: the tool's arguments, else the whole payload. */
  details: unknown;
  waiting: string;
  expires: { at: string; in: string | null; soon: boolean } | null;
  taskId: string | null;
  runId: string | null;
  /** A decision task (an article to approve) can be sent back; a paused agent run cannot. */
  canSendBack: boolean;
  decision: { by: string; at: string; reason: string | null } | null;
}

function actorName(actor: Record<string, unknown> | null, agents: Record<string, AgentState>): string {
  if (!actor) return "—";
  const id = String(actor.id ?? "");
  if (actor.kind === "agent") return agents[id]?.display_name ?? `代理 ${id.slice(0, 8)}`;
  return `${ACTOR_KIND[String(actor.kind)] ?? String(actor.kind)} ${id}`.trim();
}

export function approvalCard(approval: Approval, agents: Record<string, AgentState>, now: Date): ApprovalCard {
  const payload = approval.payload as Record<string, unknown>;
  const expiresMs = approval.expires_at ? Date.parse(approval.expires_at) - now.getTime() : null;
  return {
    id: approval.id,
    state: approval.state,
    kind: KIND_LABEL[approval.kind] ?? approval.kind,
    action: approval.action,
    summary: approval.summary,
    requester: actorName(approval.requested_by, agents),
    details: payload.args ?? payload,
    waiting: formatDuration(Math.max(0, now.getTime() - Date.parse(approval.created_at))),
    expires: approval.expires_at
      ? {
          at: approval.expires_at,
          in: expiresMs !== null && expiresMs > 0 ? formatDuration(expiresMs) : null,
          soon: expiresMs !== null && expiresMs < 60 * 60 * 1000,
        }
      : null,
    taskId: approval.task_id,
    runId: approval.run_id,
    canSendBack: Boolean(approval.task_id) && !approval.run_id,
    decision: approval.decided_at
      ? { by: actorName(approval.decided_by, agents), at: approval.decided_at, reason: approval.reason }
      : null,
  };
}

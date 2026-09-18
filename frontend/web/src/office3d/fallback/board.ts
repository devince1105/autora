// The 2D board's model (T-410, 04 §8): the same store and the same visual mapping as the 3D
// office, as cards in rows that follow the floor plan. Pure functions; the component only draws.
import type { EventEnvelope } from "@autora/event-schema";

import type { AgentState, RealtimeState } from "@/stores/realtime";

import { ROLE_COLOR } from "../palette";
import { assignSeats, type ZoneId } from "../scene/layout";
import { ROLE_LABEL, visualForAgent, type VisualState } from "../visual/mapping";

export type RowId = "ceo" | "work" | "front" | "other";

export const ROW_LABEL: Record<RowId, string> = {
  ceo: "執行長室",
  work: "工作區",
  front: "前排",
  other: "未排座位",
};

const ROW_OF: Record<ZoneId, RowId> = { ceo: "ceo", research: "work", editorial: "work", growth: "front", spare: "front" };

export interface BoardCard {
  id: string;
  name: string;
  role: string;
  roleLabel: string;
  color: string;
  visual: VisualState;
  taskName: string | null;
  since: string;
  sinceMs: number;
  lastDone: { summary: string | null; taskName: string | null; at: string } | null;
}

export interface BoardRow {
  id: RowId;
  label: string;
  cards: BoardCard[];
}

const str = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export function formatSince(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} 分` : `${Math.floor(m / 60)} 小時 ${m % 60} 分`;
}

/** The agent's latest completed run in the buffer: what it finished most recently. */
function lastCompletion(agentId: string, events: readonly EventEnvelope[], company: RealtimeState) {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.event_type === "AGENT_RUN_COMPLETED" && e.agent_id === agentId) {
      const payload = e.payload as Record<string, unknown>;
      return {
        summary: str(payload.output_summary),
        taskName: e.task_id ? (company.tasks[e.task_id]?.display_name ?? null) : null,
        at: e.occurred_at,
      };
    }
  }
  return null;
}

function card(agent: AgentState, company: RealtimeState, now: Date): BoardCard | null {
  const visual = visualForAgent(agent, now);
  if (!visual || !agent.activity) return null;
  const sinceMs = now.getTime() - Date.parse(agent.activity.since);
  return {
    id: agent.id,
    name: agent.display_name,
    role: agent.role,
    roleLabel: ROLE_LABEL[agent.role] ?? agent.role,
    color: ROLE_COLOR[agent.role] ?? ROLE_COLOR.spare,
    visual,
    taskName: str(agent.activity.detail.task_name),
    since: formatSince(sinceMs),
    sinceMs,
    lastDone: lastCompletion(agent.id, company.recentEvents, company),
  };
}

/** Cards in floor-plan rows (back to front, left to right), empty rows left out. */
export function boardModel(company: RealtimeState | null, now: Date): BoardRow[] {
  if (!company) return [];
  const agents = Object.values(company.agents);
  const { seats, unseated } = assignSeats(agents);
  const rows: Record<RowId, { card: BoardCard; x: number }[]> = { ceo: [], work: [], front: [], other: [] };
  for (const agent of agents) {
    const c = card(agent, company, now);
    if (!c) continue;
    const seat = seats.get(agent.id);
    if (seat) rows[ROW_OF[seat.zone]].push({ card: c, x: seat.desk[0] });
    else if (unseated.includes(agent.id)) rows.other.push({ card: c, x: 0 });
  }
  return (Object.keys(rows) as RowId[])
    .filter((id) => rows[id].length)
    .map((id) => ({
      id,
      label: ROW_LABEL[id],
      cards: rows[id].sort((a, b) => a.x - b.x || a.card.name.localeCompare(b.card.name)).map((r) => r.card),
    }));
}

export interface Handoff {
  seq: number;
  from: string;
  to: string[];
}

/**
 * Hand-offs in events after `afterSeq`: a completed run whose handoff names a role, from its agent
 * to the company's agents of that role (the next task goes to one of them).
 */
export function handoffsAfter(events: readonly EventEnvelope[], afterSeq: number, agents: Record<string, AgentState>): Handoff[] {
  const out: Handoff[] = [];
  for (const e of events) {
    if (e.seq === null || e.seq <= afterSeq || e.event_type !== "AGENT_RUN_COMPLETED" || !e.agent_id) continue;
    const roles = ((e.payload as { handoff?: { to_role: string }[] }).handoff ?? []).map((h) => h.to_role);
    const to = Object.values(agents)
      .filter((a) => roles.includes(a.role) && a.id !== e.agent_id)
      .map((a) => a.id);
    if (to.length) out.push({ seq: e.seq, from: e.agent_id, to });
  }
  return out;
}

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * The hand-off arrow between two cards: an arc from the top of one to the top of the other,
 * rising above them, so it is visible even between neighbours and never covers their text.
 */
export function arcBetween(from: Box, to: Box, lift = 36): { d: string; peakY: number } {
  const x1 = from.left + from.width / 2;
  const y1 = from.top - 4;
  const x2 = to.left + to.width / 2;
  const y2 = to.top - 4;
  const peakY = Math.min(y1, y2) - lift - Math.abs(x2 - x1) * 0.08;
  return { d: `M ${x1} ${y1} Q ${(x1 + x2) / 2} ${peakY} ${x2} ${y2}`, peakY };
}

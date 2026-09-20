// The 2D board's model (T-410, 04 §8): the same store and the same visual mapping as the 3D
// office, as cards in rows that follow the floor plan. Pure functions; the component only draws.
import type { EventEnvelope } from "@autora/event-schema";

import type { AgentState, RealtimeState } from "@/stores/realtime";

import { ROLE_COLOR } from "../palette";
import { assignSeats } from "../scene/layout";
import { ROLE_LABEL, visualForAgent, type VisualState } from "../visual/mapping";

export type RowId = string;

/** The row an agent with no department falls into; the only one this file names itself. */
export const UNPLACED = "unplaced";

export const DEPARTMENT_LABEL: Record<string, string> = {
  [UNPLACED]: "未編制",
  // the zones the floor plan draws, for a company whose departments are not on the chart yet
  ceo: "執行長室",
  research: "研究區",
  editorial: "編輯區",
  growth: "行銷區",
  spare: "彈性座位",
};

/**
 * A department's name is the company's to give (ARCHITECTURE_V2 §14.7): the org chart's name
 * when the page has it, else the floor plan's own name for that room, else the key. The office
 * does not invent names for a company's departments.
 */
function rowLabel(id: RowId, names: Readonly<Record<string, string>>): string {
  return names[id] ?? DEPARTMENT_LABEL[id] ?? id;
}

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

/**
 * One row per department (T-600 batch 3), in floor order, empty rows left out.
 *
 * The 3D office draws departments as rooms; without WebGL the same organisation is a list of
 * them, so the two views answer "who works where" the same way. An agent the org chart does
 * not place yet gets its own row rather than being dropped — the office shows what is there.
 */
export function boardModel(
  company: RealtimeState | null,
  now: Date,
  names: Readonly<Record<string, string>> = {},
): BoardRow[] {
  if (!company) return [];
  const agents = Object.values(company.agents);
  const { seats } = assignSeats(agents);
  const rows = new Map<RowId, { card: BoardCard; x: number; order: number }[]>();
  for (const agent of agents) {
    const c = card(agent, company, now);
    if (!c) continue;
    const seat = seats.get(agent.id);
    const id = agent.department_key ?? seat?.zone ?? UNPLACED;
    // by the desk when it has one, so a row reads left to right as the room does
    const entry = { card: c, x: seat?.desk[0] ?? 0, order: seat ? 0 : 1 };
    rows.set(id, [...(rows.get(id) ?? []), entry]);
  }
  return [...rows]
    .sort(([a, left], [b, right]) => floorOrder(left) - floorOrder(right) || a.localeCompare(b))
    .map(([id, entries]) => ({
      id,
      label: rowLabel(id, names),
      cards: entries
        .sort((l, r) => l.order - r.order || l.x - r.x || l.card.name.localeCompare(r.card.name))
        .map((entry) => entry.card),
    }));
}

/** Rooms in the order the floor plan lays them out; unplaced agents last. */
function floorOrder(entries: { x: number; order: number }[]): number {
  return entries.some((e) => e.order === 0) ? Math.min(...entries.map((e) => e.x)) : 1000;
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

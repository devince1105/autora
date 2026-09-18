// Event timeline (T-312, AC-8): the realtime store's recent events (a ring buffer of at most
// RECENT_EVENTS_KEPT), newest first, filtered by agent and by type. Pausing is the view's
// business (Timeline.tsx); these are pure functions over the events it shows.
import type { EventEnvelope } from "@autora/event-schema";

import { describeEvent, type Tone } from "@/features/events/describe";
import type { AgentState } from "@/realtime/reducer";
import type { TimelineFilters } from "@/stores/ui";

export interface TimelineItem {
  key: string;
  seq: number;
  at: string;
  eventType: string;
  label: string;
  tone: Tone;
  summary: string | null;
  /** Agent name, or who else acted (system / human). */
  actor: string;
  agentId: string | null;
  taskId: string | null;
  runId: string | null;
  payload: unknown;
}

const ACTOR_KIND: Record<string, string> = { system: "系統", human: "人員", agent: "代理" };

export function matches(event: EventEnvelope, filters: TimelineFilters): boolean {
  if (filters.agentIds.length && !(event.agent_id && filters.agentIds.includes(event.agent_id))) return false;
  if (filters.eventTypes.length && !filters.eventTypes.includes(event.event_type)) return false;
  return true;
}

/** Filtered events, newest (highest seq) first. */
export function timelineItems(
  events: readonly EventEnvelope[],
  agents: Record<string, AgentState>,
  filters: TimelineFilters,
): TimelineItem[] {
  return events
    .filter((event) => event.seq !== null && matches(event, filters))
    .sort((a, b) => b.seq! - a.seq!)
    .map((event) => {
      const { label, tone, summary } = describeEvent(event.event_type, event.payload);
      const agent = event.agent_id ? agents[event.agent_id] : undefined;
      return {
        key: event.event_id,
        seq: event.seq!,
        at: event.occurred_at,
        eventType: event.event_type,
        label,
        tone,
        summary,
        actor: agent?.display_name ?? ACTOR_KIND[event.actor.kind] ?? event.actor.kind,
        agentId: event.agent_id,
        taskId: event.task_id,
        runId: event.run_id,
        payload: event.payload,
      };
    });
}

/** What can be filtered on: the company's agents and the types in the buffer, plus what is selected. */
export function filterOptions(
  events: readonly EventEnvelope[],
  agents: Record<string, AgentState>,
  filters: TimelineFilters,
): { agents: { id: string; name: string }[]; types: string[] } {
  const agentIds = new Set([...Object.keys(agents), ...filters.agentIds]);
  const types = new Set([...events.map((e) => e.event_type), ...filters.eventTypes]);
  return {
    agents: [...agentIds]
      .map((id) => ({ id, name: agents[id]?.display_name ?? id.slice(0, 8) }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    types: [...types].sort(),
  };
}

/** Events after `seq` that pass the filters: what arrived while the view was paused. */
export function countNewer(events: readonly EventEnvelope[], seq: number, filters: TimelineFilters): number {
  return events.filter((e) => e.seq !== null && e.seq > seq && matches(e, filters)).length;
}

export function toggle(list: readonly string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

"use client";

import type { EventEnvelope } from "@autora/event-schema";
import { useState } from "react";

import type { AgentState } from "@/realtime/reducer";
import { uiStore, useUi } from "@/stores/ui";

import { countNewer, filterOptions, timelineItems, toggle } from "./model";
import { TimelineView } from "./TimelineView";

/**
 * The timeline over the live events. Pausing freezes what is shown (the store keeps applying
 * events underneath, so nothing is lost) and counts what arrived meanwhile; filters still apply
 * to the frozen list.
 */
export function Timeline({
  companyId,
  events,
  agents,
}: {
  companyId: string;
  events: readonly EventEnvelope[];
  agents: Record<string, AgentState>;
}) {
  const paused = useUi((state) => state.timelinePaused);
  const filters = useUi((state) => state.filters);
  const [frozen, setFrozen] = useState<{ companyId: string; events: readonly EventEnvelope[] } | null>(null);
  // Adjust state while rendering (react.dev "storing information from previous renders").
  if (paused && frozen?.companyId !== companyId) setFrozen({ companyId, events });
  if (!paused && frozen !== null) setFrozen(null);

  const shown = paused && frozen?.companyId === companyId ? frozen.events : events;
  const lastShown = shown.reduce((max, e) => Math.max(max, e.seq ?? 0), 0);
  const { setTimelinePaused, setFilters } = uiStore.getState();
  return (
    <TimelineView
      items={timelineItems(shown, agents, filters)}
      buffered={events.length}
      options={filterOptions(events, agents, filters)}
      filters={filters}
      paused={paused}
      newWhilePaused={paused ? countNewer(events, lastShown, filters) : 0}
      onPause={setTimelinePaused}
      onToggleAgent={(id) => setFilters({ agentIds: toggle(filters.agentIds, id) })}
      onToggleType={(type) => setFilters({ eventTypes: toggle(filters.eventTypes, type) })}
      onClearFilters={() => setFilters({ agentIds: [], eventTypes: [] })}
    />
  );
}

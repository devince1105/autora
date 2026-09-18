// Events keep server state fresh (T-308, 3d-office/05 §5): every event the realtime store
// applies is mapped to the query keys it makes stale, and those are invalidated (refetched if
// a page shows them). One table, so "what refreshes when" is visible in one place.
import type { EventEnvelope } from "@autora/event-schema";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import type { StoreApi } from "zustand/vanilla";

import type { RealtimeStoreState } from "@/stores/realtime";

import { queryKeys } from "./queries";

const RUN_CHANGES = new Set([
  "AGENT_RUN_STARTED",
  "AGENT_RUN_COMPLETED",
  "AGENT_RUN_FAILED",
  "AGENT_RUN_ABORTED",
  "TASK_STARTED",
  "TASK_WAITING",
  "TASK_SUCCEEDED",
  "TASK_FAILED",
  "TASK_CANCELLED",
  "TASK_BLOCKED",
]);
const AGENT_LIST_CHANGES = new Set(["AGENT_CREATED", "AGENT_PAUSED", "AGENT_RESUMED"]);
/** A run's model spend is final when it ends; ledger and goal events change the rest. */
const KPI_CHANGES = new Set([
  "AGENT_RUN_COMPLETED",
  "AGENT_RUN_FAILED",
  "AGENT_RUN_ABORTED",
  "EXPENSE_RECORDED",
  "REVENUE_RECORDED",
  "GOAL_CREATED",
  "GOAL_UPDATED",
  "KPI_SNAPSHOT_CREATED",
]);

/** Query keys an event makes stale. Keys are prefixes: ["approvals", c] covers every state. */
export function eventToQueryKeys(event: EventEnvelope): QueryKey[] {
  const keys: QueryKey[] = [];
  if (event.run_id) {
    keys.push(queryKeys.trace(event.run_id)); // every event of a run is a line of its trace
    if (RUN_CHANGES.has(event.event_type)) keys.push(queryKeys.run(event.run_id));
  }
  if (event.task_id && event.event_type.startsWith("TASK_")) {
    keys.push(queryKeys.task(event.task_id));
  }
  if (event.event_type.startsWith("APPROVAL_")) {
    keys.push(["approvals", event.company_id]);
  }
  if (AGENT_LIST_CHANGES.has(event.event_type)) {
    keys.push(queryKeys.agents(event.company_id));
  }
  if (KPI_CHANGES.has(event.event_type)) {
    keys.push(queryKeys.kpis(event.company_id));
  }
  return keys;
}

/**
 * Invalidate queries for every event the store applies. Keys are collected per store update
 * and invalidated once each (a backlog of 200 events invalidates a run's trace once, not 200
 * times). Returns a function that stops listening.
 */
export function connectQueryInvalidation(
  queryClient: QueryClient,
  store: StoreApi<RealtimeStoreState>,
): () => void {
  let lastSeq = store.getState().company?.lastSeq ?? 0;
  return store.subscribe((state, previous) => {
    const company = state.company;
    if (!company) return;
    if (company !== previous.company && company.companyId !== previous.company?.companyId) {
      lastSeq = company.lastSeq; // a new company (or first hydrate): nothing to catch up
      return;
    }
    if (company.lastSeq < lastSeq) lastSeq = 0; // re-hydrated to an older state
    if (company.lastSeq === lastSeq) return;
    const keys = new Map<string, QueryKey>();
    for (const event of company.recentEvents) {
      if ((event.seq ?? 0) <= lastSeq) continue;
      for (const key of eventToQueryKeys(event)) keys.set(JSON.stringify(key), key);
    }
    lastSeq = company.lastSeq;
    for (const queryKey of keys.values()) void queryClient.invalidateQueries({ queryKey });
  });
}

// The realtime snapshot (GET /api/companies/{id}/realtime/snapshot, T-301).
// Mirrors backend/autora/realtime/projection.py. Validated at the boundary so a malformed
// response is rejected in one place instead of breaking the store later.
import { z } from "zod";

export const ACTIVITY_STATES = [
  "IDLE",
  "THINKING",
  "WORKING",
  "WAITING",
  "REVIEWING",
  "COMPLETED",
  "FAILED",
  "PAUSED",
] as const;
export const ActivityState = z.enum(ACTIVITY_STATES);
export type ActivityState = z.infer<typeof ActivityState>;

export const ActivityView = z.object({
  /** Effective state at the snapshot's server_time (COMPLETED past display_until reads IDLE). */
  state: ActivityState,
  stored_state: ActivityState,
  detail: z.record(z.string(), z.unknown()),
  since: z.string(),
  run_id: z.uuid().nullable(),
  task_id: z.uuid().nullable(),
  last_event_seq: z.number().int(),
});
export type ActivityView = z.infer<typeof ActivityView>;

export const AgentView = z.object({
  id: z.uuid(),
  role: z.string(),
  display_name: z.string(),
  avatar_key: z.string(),
  activity: ActivityView,
});
export type AgentView = z.infer<typeof AgentView>;

export const TaskView = z.object({
  id: z.uuid(),
  name: z.string(),
  display_name: z.string(),
  required_role: z.string(),
  state: z.string(),
  depends_on: z.array(z.uuid()),
  workflow_run_id: z.uuid().nullable(),
  attempt: z.number().int(),
  run_id: z.uuid().nullable(),
  agent_id: z.uuid().nullable(),
  since: z.string(),
  last_event_seq: z.number().int(),
});
export type TaskView = z.infer<typeof TaskView>;

export const RealtimeSnapshot = z.object({
  company_id: z.uuid(),
  last_seq: z.number().int().min(0),
  server_time: z.string(),
  agents: z.array(AgentView),
  tasks: z.array(TaskView),
  /** Parsed one by one with parseEvent: an event of a newer type is dropped, not fatal. */
  recent_events: z.array(z.unknown()),
  kpis: z.record(z.string(), z.unknown()).nullable(),
  cycle: z.record(z.string(), z.unknown()).nullable(),
});
export type RealtimeSnapshot = z.infer<typeof RealtimeSnapshot>;

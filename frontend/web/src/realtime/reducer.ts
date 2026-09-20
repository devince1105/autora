// Realtime reducer (T-305): snapshot + events -> company state, as pure functions.
//
// This is the TypeScript twin of backend/autora/realtime/reducer.py; both must turn the same
// snapshot and events into the same projection (checked against the fixture the Python
// contract test writes: src/realtime/__fixtures__/contract.json). The rules, briefly:
//
// - Events with seq <= lastSeq, without seq, or of another company are ignored (duplicates at
//   the backlog/live boundary). The gateway guarantees order and completeness per connection
//   (05 §4 as amended in T-303), so no gap detection happens here.
// - AGENT_CREATED adds an agent (with the department it was hired into), AGENT_ASSIGNED moves
//   it to another role and room, AGENT_RETIRED removes one. Activity events set stored_state from the event type, detail
//   = payload + run/task/workflow context (none for IDLE and PAUSED; task_name is the task's
//   display name), since changes only when the state does. A non-final AGENT_RUN_FAILED or
//   AGENT_RUN_ABORTED is trace data and changes nothing.
// - TASK_CREATED adds a task (PENDING); TASK_* set its state, since and lastEventSeq;
//   TASK_STARTED also sets attempt, run and agent.
// - view(now): effective activity state (COMPLETED past display_until reads IDLE), finished tasks
//   older than 10 minutes left out, the last 100 events. Those tasks also leave the state itself
//   as later events arrive (T-412: the store must not grow with history).
//
// No visual state lives here (05 §5): poses, bubbles and animation are derived elsewhere.
import { parseEvent, type EventEnvelope } from "@autora/event-schema";

import type {
  ActivityState,
  ActivityView,
  RealtimeSnapshot,
  TaskView,
} from "./snapshot";

export const FINISHED_TASK_WINDOW_MS = 10 * 60 * 1000;
export const RECENT_EVENTS_VIEW = 100;
export const RECENT_EVENTS_KEPT = 500;

export interface LiveProgress {
  runId: string | null;
  stepSeq: number;
  tokensSoFar: number;
  progress: { label: string; current: number; target: number | null } | null;
  at: string;
}

export interface AgentState {
  id: string;
  role: string;
  display_name: string;
  avatar_key: string;
  department_id: string | null;
  department_key: string | null;
  /** null until the agent's first activity event (right after AGENT_CREATED). */
  activity: ActivityView | null;
  /** Latest ephemeral AGENT_STEP_PROGRESS of the agent's current run; never persisted. */
  liveProgress: LiveProgress | null;
}

export interface RealtimeState {
  companyId: string;
  lastSeq: number;
  agents: Record<string, AgentState>;
  tasks: Record<string, TaskView>;
  recentEvents: EventEnvelope[];
}

/** What load_snapshot returns: the comparable projection at a moment. */
export interface Projection {
  company_id: string;
  last_seq: number;
  agents: (Omit<AgentState, "liveProgress" | "activity"> & {
    activity: ActivityView;
  })[];
  tasks: TaskView[];
  recent_events: EventEnvelope[];
}

const ACTIVITY_BY_TYPE: Partial<
  Record<EventEnvelope["event_type"], ActivityState>
> = {
  AGENT_IDLE: "IDLE",
  AGENT_RESUMED: "IDLE",
  AGENT_THINKING: "THINKING",
  AGENT_WORKING: "WORKING",
  AGENT_WAITING: "WAITING",
  AGENT_REVIEWING: "REVIEWING",
  AGENT_RUN_COMPLETED: "COMPLETED",
  AGENT_RUN_FAILED: "FAILED",
  AGENT_RUN_ABORTED: "FAILED",
  AGENT_PAUSED: "PAUSED",
};
const RUNLESS: ReadonlySet<ActivityState> = new Set(["IDLE", "PAUSED"]);
const TASK_STATE_BY_TYPE: Partial<Record<EventEnvelope["event_type"], string>> =
  {
    TASK_READY: "READY",
    TASK_STARTED: "RUNNING",
    TASK_WAITING: "WAITING_APPROVAL",
    TASK_SUCCEEDED: "SUCCEEDED",
    TASK_CANCELLED: "CANCELLED",
    TASK_BLOCKED: "BLOCKED_BUDGET",
  };
const FINISHED: ReadonlySet<string> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);

// --- hydrate --------------------------------------------------------------------------------

export function hydrate(snapshot: RealtimeSnapshot): RealtimeState {
  const agents: Record<string, AgentState> = {};
  for (const agent of snapshot.agents) {
    agents[agent.id] = {
      ...agent,
      activity: { ...agent.activity },
      liveProgress: null,
    };
  }
  const tasks: Record<string, TaskView> = {};
  for (const task of snapshot.tasks) tasks[task.id] = { ...task };
  const recentEvents: EventEnvelope[] = [];
  for (const raw of snapshot.recent_events) {
    const parsed = parseEvent(raw);
    if (parsed.ok) recentEvents.push(parsed.event);
  }
  return {
    companyId: snapshot.company_id,
    lastSeq: snapshot.last_seq,
    agents,
    tasks,
    recentEvents,
  };
}

// --- apply ----------------------------------------------------------------------------------

/**
 * Apply one persisted event. Returns the same object when the event is ignored.
 *
 * Tasks that finished more than FINISHED_TASK_WINDOW_MS before the event are dropped from the
 * state (04 §7: the store must not grow with history; view() already leaves them out, so the
 * projection is unchanged). Measured from the event's time, not the local clock, so replaying
 * the same events gives the same state.
 */
export function applyEvent(
  state: RealtimeState,
  event: EventEnvelope,
): RealtimeState {
  const next = applyOne(state, event);
  return next === state
    ? state
    : pruneFinishedTasks(next, Date.parse(event.occurred_at));
}

function pruneFinishedTasks(state: RealtimeState, at: number): RealtimeState {
  let tasks: Record<string, TaskView> | null = null;
  for (const [id, task] of Object.entries(state.tasks)) {
    if (
      FINISHED.has(task.state) &&
      Date.parse(task.since) <= at - FINISHED_TASK_WINDOW_MS
    ) {
      tasks ??= { ...state.tasks };
      delete tasks[id];
    }
  }
  return tasks ? { ...state, tasks } : state;
}

function applyOne(state: RealtimeState, event: EventEnvelope): RealtimeState {
  if (
    event.seq === null ||
    event.seq <= state.lastSeq ||
    event.company_id !== state.companyId
  ) {
    return state;
  }
  const next: RealtimeState = {
    ...state,
    lastSeq: event.seq,
    recentEvents: [...state.recentEvents, event].slice(-RECENT_EVENTS_KEPT),
  };

  if (event.event_type === "AGENT_CREATED" && event.agent_id) {
    next.agents = {
      ...state.agents,
      [event.agent_id]: {
        id: event.agent_id,
        role: event.payload.role,
        display_name: event.payload.display_name,
        avatar_key: event.payload.avatar_key,
        department_id: event.payload.department_id ?? null,
        department_key: event.payload.department_key ?? null,
        activity: null,
        liveProgress: null,
      },
    };
    return next;
  }

  if (
    event.event_type === "AGENT_ASSIGNED" &&
    event.agent_id &&
    state.agents[event.agent_id]
  ) {
    // the only event that moves a drawn agent to another room without a full reload
    next.agents = {
      ...state.agents,
      [event.agent_id]: {
        ...state.agents[event.agent_id],
        role: event.payload.role,
        department_id: event.payload.department_id,
        department_key: event.payload.department_key ?? null,
      },
    };
    return next;
  }

  if (event.event_type === "AGENT_RETIRED" && event.agent_id) {
    const { [event.agent_id]: gone, ...rest } = state.agents; // off the roster
    void gone;
    next.agents = rest;
    return next;
  }

  const activityState = ACTIVITY_BY_TYPE[event.event_type];
  if (activityState && event.agent_id && state.agents[event.agent_id]) {
    const payload = event.payload as { final?: boolean };
    const traceOnly =
      (event.event_type === "AGENT_RUN_FAILED" ||
        event.event_type === "AGENT_RUN_ABORTED") &&
      payload.final === false;
    if (!traceOnly) {
      const agent = state.agents[event.agent_id];
      next.agents = {
        ...state.agents,
        [agent.id]: withActivity(agent, activityState, event, state),
      };
    }
    return next;
  }

  if (event.event_type === "TASK_CREATED" && event.task_id) {
    next.tasks = {
      ...state.tasks,
      [event.task_id]: {
        id: event.task_id,
        name: event.payload.name,
        display_name: event.payload.display_name,
        required_role: event.payload.required_role,
        state: "PENDING",
        depends_on: [...event.payload.depends_on],
        workflow_run_id: event.payload.workflow_run_id,
        attempt: 0,
        run_id: null,
        agent_id: null,
        since: event.occurred_at,
        last_event_seq: event.seq,
      },
    };
    return next;
  }

  if (
    event.event_type.startsWith("TASK_") &&
    event.task_id &&
    state.tasks[event.task_id]
  ) {
    next.tasks = {
      ...state.tasks,
      [event.task_id]: withTaskEvent(state.tasks[event.task_id], event),
    };
  }
  return next;
}

function withActivity(
  agent: AgentState,
  state: ActivityState,
  event: EventEnvelope,
  current: RealtimeState,
): AgentState {
  const runless = RUNLESS.has(state);
  const taskName =
    runless || !event.task_id
      ? null
      : (current.tasks[event.task_id]?.display_name ?? null);
  const context: Record<string, string | null> = {
    run_id: runless ? null : event.run_id,
    task_id: runless ? null : event.task_id,
    workflow_run_id: runless ? null : event.workflow_run_id,
    task_name: taskName,
  };
  const detail: Record<string, unknown> = {
    ...(event.payload as Record<string, unknown>),
  };
  for (const [key, value] of Object.entries(context))
    if (value) detail[key] = value;

  const previous = agent.activity;
  const activity: ActivityView = {
    state,
    stored_state: state,
    detail,
    since:
      previous && previous.stored_state === state
        ? previous.since
        : event.occurred_at,
    run_id: context.run_id,
    task_id: context.task_id,
    last_event_seq: event.seq as number,
  };
  const liveProgress =
    agent.liveProgress && agent.liveProgress.runId === activity.run_id
      ? agent.liveProgress
      : null;
  return { ...agent, activity, liveProgress };
}

function withTaskEvent(task: TaskView, event: EventEnvelope): TaskView {
  const next: TaskView = {
    ...task,
    since: event.occurred_at,
    last_event_seq: event.seq as number,
  };
  if (event.event_type === "TASK_FAILED") {
    if (event.payload.final) next.state = "FAILED";
    return next; // a retried failure is followed by TASK_READY in the same transaction
  }
  const target = TASK_STATE_BY_TYPE[event.event_type];
  if (target) next.state = target;
  if (event.event_type === "TASK_STARTED") {
    next.attempt = event.payload.attempt;
    next.run_id = event.payload.run_id;
    next.agent_id = event.payload.agent_id;
  }
  return next;
}

// --- ephemeral ------------------------------------------------------------------------------

export interface EphemeralMessage {
  event_type: string;
  agent_id: string | null;
  run_id: string | null;
  occurred_at?: string | null;
  payload: Record<string, unknown>;
}

/** Live progress (no seq, never part of the projection). Unknown kinds are ignored. */
export function applyEphemeral(
  state: RealtimeState,
  message: EphemeralMessage,
): RealtimeState {
  if (message.event_type !== "AGENT_STEP_PROGRESS" || !message.agent_id)
    return state;
  const agent = state.agents[message.agent_id];
  if (!agent) return state;
  const payload = message.payload as {
    step_seq?: number;
    tokens_so_far?: number;
    progress?: LiveProgress["progress"];
  };
  const liveProgress: LiveProgress = {
    runId: message.run_id,
    stepSeq: payload.step_seq ?? 0,
    tokensSoFar: payload.tokens_so_far ?? 0,
    progress: payload.progress ?? null,
    at: message.occurred_at ?? new Date().toISOString(),
  };
  return {
    ...state,
    agents: { ...state.agents, [agent.id]: { ...agent, liveProgress } },
  };
}

// --- view -----------------------------------------------------------------------------------

export function effectiveState(
  activity: ActivityView,
  now: Date,
): ActivityState {
  if (activity.stored_state === "COMPLETED") {
    const until = activity.detail.display_until;
    if (typeof until === "string" && Date.parse(until) <= now.getTime())
      return "IDLE";
  }
  return activity.stored_state;
}

export function isTaskVisible(task: TaskView, now: Date): boolean {
  return (
    !FINISHED.has(task.state) ||
    Date.parse(task.since) > now.getTime() - FINISHED_TASK_WINDOW_MS
  );
}

/** The projection at `now`: what the snapshot endpoint would return at that moment. */
export function view(state: RealtimeState, now: Date): Projection {
  const agents: Projection["agents"] = [];
  for (const agent of Object.values(state.agents)) {
    if (!agent.activity) continue;
    agents.push({
      id: agent.id,
      role: agent.role,
      display_name: agent.display_name,
      avatar_key: agent.avatar_key,
      department_id: agent.department_id,
      department_key: agent.department_key,
      activity: {
        ...agent.activity,
        state: effectiveState(agent.activity, now),
      },
    });
  }
  return {
    company_id: state.companyId,
    last_seq: state.lastSeq,
    agents,
    tasks: Object.values(state.tasks).filter((task) =>
      isTaskVisible(task, now),
    ),
    recent_events: state.recentEvents.slice(-RECENT_EVENTS_VIEW),
  };
}

import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  applyEphemeral,
  applyEvent,
  effectiveState,
  hydrate,
  view,
  type Projection,
  type RealtimeState,
} from "./reducer";
import { RealtimeSnapshot } from "./snapshot";

// Written by the Python contract test from a randomised real runtime history (make
// realtime-fixture): the first snapshot, every event after it, and the last snapshot.
const fixture = JSON.parse(
  readFileSync(new URL("./__fixtures__/contract.json", import.meta.url), "utf8"),
) as { snapshot_before: unknown; events: unknown[]; snapshot_after: unknown };

const before = RealtimeSnapshot.parse(fixture.snapshot_before);
const after = RealtimeSnapshot.parse(fixture.snapshot_after);
const events: EventEnvelope[] = fixture.events.map((raw) => {
  const parsed = parseEvent(raw);
  if (!parsed.ok) throw new Error(`fixture event ${parsed.eventType}: ${parsed.error}`);
  return parsed.event;
});

function replay(start = before, list = events): RealtimeState {
  return list.reduce(applyEvent, hydrate(start));
}

/** The comparable content of a projection (same as canonical() in reducer.py). */
function canonical(p: {
  company_id: string;
  last_seq: number;
  agents: { id: string }[];
  tasks: { id: string }[];
  recent_events: unknown[];
}) {
  return {
    company_id: p.company_id,
    last_seq: p.last_seq,
    agents: Object.fromEntries(p.agents.map((a) => [a.id, a])),
    tasks: Object.fromEntries(p.tasks.map((t) => [t.id, t])),
    recent_event_seqs: p.recent_events.map((e) => (e as { seq: number }).seq),
  };
}

describe("cross-language contract (T-302 fixture)", () => {
  it("is a real, varied history", () => {
    const types = new Set(events.map((e) => e.event_type));
    expect(events.length).toBeGreaterThan(50);
    expect(types.size).toBeGreaterThanOrEqual(15);
  });

  it("hydrate(before) + events == after, like the Python reducer", () => {
    const projection: Projection = view(replay(), new Date(after.server_time));
    expect(canonical(projection)).toEqual(canonical(after));
  });

  it("every prefix is consistent: last_seq and recent events follow the events", () => {
    const state = replay(before, events.slice(0, 40));
    expect(state.lastSeq).toBe(events[39].seq);
    expect(state.recentEvents.at(-1)?.seq).toBe(events[39].seq);
  });
});

describe("ignored events", () => {
  it("duplicates and older events change nothing (backlog/live overlap)", () => {
    const state = replay();
    for (const event of events.slice(-5)) expect(applyEvent(state, event)).toBe(state);
  });

  it("events of another company or without seq change nothing", () => {
    const state = hydrate(before);
    const event = events[0];
    expect(applyEvent(state, { ...event, company_id: crypto.randomUUID() })).toBe(state);
    expect(applyEvent(state, { ...event, seq: null })).toBe(state);
  });

  it("a non-final run abort is trace data, a final one fails the agent", () => {
    const state = hydrate(after);
    const agent = after.agents[0];
    const abort = (final: boolean): EventEnvelope => {
      const parsed = parseEvent({
        event_id: crypto.randomUUID(),
        seq: state.lastSeq + 1,
        event_type: "AGENT_RUN_ABORTED",
        schema_version: 1,
        company_id: state.companyId,
        occurred_at: new Date().toISOString(),
        aggregate_type: "agent_run",
        aggregate_id: crypto.randomUUID(),
        agent_id: agent.id,
        actor: { kind: "system", id: "task_manager" },
        payload: { reason: "timeout", final },
      });
      if (!parsed.ok) throw new Error(parsed.error);
      return parsed.event;
    };
    expect(applyEvent(state, abort(false)).agents[agent.id].activity).toEqual(
      state.agents[agent.id].activity,
    );
    expect(applyEvent(state, abort(true)).agents[agent.id].activity?.stored_state).toBe("FAILED");
  });
});

describe("clock rules", () => {
  it("COMPLETED reads IDLE after display_until; finished tasks leave after 10 minutes", () => {
    const state = replay();
    const completed = Object.values(state.agents).find(
      (a) => a.activity?.stored_state === "COMPLETED",
    );
    const later = new Date(Date.parse(after.server_time) + 11 * 60 * 1000);
    if (completed?.activity) {
      expect(effectiveState(completed.activity, later)).toBe("IDLE");
    }
    const finishedNow = view(state, new Date(after.server_time)).tasks.length;
    const finishedLater = view(state, later).tasks.filter((t) =>
      ["SUCCEEDED", "FAILED", "CANCELLED"].includes(t.state),
    ).length;
    expect(finishedNow).toBeGreaterThan(0);
    expect(finishedLater).toBe(0);
  });
});

describe("live progress (ephemeral)", () => {
  it("attaches to the agent, is not part of the projection, and clears when the run changes", () => {
    const state = replay();
    const agent = Object.values(state.agents)[0];
    const runId = crypto.randomUUID();
    const withProgress = applyEphemeral(state, {
      event_type: "AGENT_STEP_PROGRESS",
      agent_id: agent.id,
      run_id: runId,
      occurred_at: new Date().toISOString(),
      payload: { step_seq: 2, tokens_so_far: 300, progress: { label: "notes", current: 1, target: 1 } },
    });
    expect(withProgress.agents[agent.id].liveProgress).toMatchObject({
      runId, stepSeq: 2, tokensSoFar: 300, progress: { label: "notes", current: 1, target: 1 },
    });
    expect(view(withProgress, new Date()).agents).toEqual(view(state, new Date()).agents);
    expect(withProgress.lastSeq).toBe(state.lastSeq);

    // The agent's next activity belongs to another run (or none): the progress is stale.
    const idle = parseEvent({
      event_id: crypto.randomUUID(), seq: state.lastSeq + 1, event_type: "AGENT_IDLE",
      schema_version: 1, company_id: state.companyId, occurred_at: new Date().toISOString(),
      aggregate_type: "agent", aggregate_id: agent.id, agent_id: agent.id,
      actor: { kind: "system", id: "task_manager" }, payload: { reason: "run_ended" },
    });
    if (!idle.ok) throw new Error(idle.error);
    expect(applyEvent(withProgress, idle.event).agents[agent.id].liveProgress).toBeNull();
  });

  it("ignores unknown ephemeral kinds and unknown agents", () => {
    const state = replay();
    const message = { event_type: "AGENT_STEP_PROGRESS", agent_id: crypto.randomUUID(), run_id: null, payload: {} };
    expect(applyEphemeral(state, message)).toBe(state);
    expect(applyEphemeral(state, { ...message, event_type: "SOMETHING_NEW" })).toBe(state);
  });
});

import { parseEvent, type EventEnvelope } from "@autora/event-schema";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { createRealtimeStore, type AgentState } from "@/stores/realtime";

import { assignSeats } from "../scene/layout";
import { CueDirector, HANDOVER_MS, routeFor, WALK_SPEED } from "./CueRunner";
import type { VisualCue, WalkCue } from "./cues";
import { CUE_TTL_MS, cuesFor, CueQueue, FLASH_MS, MAX_WALKS_QUEUED } from "./director";

// A randomised real runtime history (T-302 contract fixture).
const fixture = JSON.parse(readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8")) as {
  snapshot_before: { agents: { id: string; role: string; display_name: string }[] };
  events: Record<string, unknown>[];
};
const events: EventEnvelope[] = fixture.events.map((raw) => {
  const parsed = parseEvent(raw);
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.event;
});
const agents = Object.fromEntries(fixture.snapshot_before.agents.map((a) => [a.id, { ...a, avatar_key: "default", activity: null, liveProgress: null }])) as Record<string, AgentState>;
const at = (e: EventEnvelope, plusMs = 0) => new Date(Date.parse(e.occurred_at) + plusMs);
const first = (type: string, where: (e: EventEnvelope) => boolean = () => true) => events.find((e) => e.event_type === type && where(e))!;
/** The roles an event hands work to, read from the event itself: the history is regenerated
 * (make realtime-fixture), so which role it happens to be is not something to hardcode. */
const handoffRoles = (e: EventEnvelope) => [...new Set(((e.payload as { handoff?: { to_role: string }[] }).handoff ?? []).map((h) => h.to_role))];
const unlockRoles = (e: EventEnvelope) => [...new Set(((e.payload as { unlocks?: { required_role: string }[] }).unlocks ?? []).map((u) => u.required_role))];
/** A real envelope turned into another event type (for types the history does not contain). */
const as = (type: string, payload: Record<string, unknown>, base = first("AGENT_THINKING")): EventEnvelope => {
  const parsed = parseEvent({ ...base, event_type: type, payload });
  if (!parsed.ok) throw new Error(parsed.error);
  return parsed.event;
};

describe("events -> cues (04 §4 table)", () => {
  it("a completed run with a hand-off walks the result to the next role's desk and back", () => {
    const done = first("AGENT_RUN_COMPLETED", (e) => handoffRoles(e).length === 1);
    const [role] = handoffRoles(done);
    expect(cuesFor(done, agents, at(done))).toEqual([
      { kind: "walk", agentId: done.agent_id, target: { role }, carry: "document", returnAfter: true, seq: done.seq },
    ]);
    // the same completion with nobody waiting on it: no walk at all
    const alone = as("AGENT_RUN_COMPLETED", { ...(done.payload as object), handoff: [] }, done);
    expect(cuesFor(alone, agents, at(alone))).toEqual([]);
  });

  it("a task that unlocks others walks to them too (a human step: to the approval desk)", () => {
    const succeeded = first("TASK_SUCCEEDED", (e) => {
      const roles = unlockRoles(e);
      // an agent's own completion: a task finished by a human has nobody to walk
      return e.agent_id !== null && roles.length === 1 && roles[0] !== "human";
    });
    expect(cuesFor(succeeded, agents, at(succeeded))).toMatchObject([
      { kind: "walk", target: { role: unlockRoles(succeeded)[0] } },
    ]);
    const toHuman = as("TASK_SUCCEEDED", { run_id: null, output_ref: null, unlocks: [{ task_id: succeeded.task_id, required_role: "human" }] }, succeeded);
    expect(cuesFor(toHuman, agents, at(toHuman))).toMatchObject([{ kind: "walk", target: { place: "approval" } }]);
  });

  it("an approval request walks to the approval desk", () => {
    // approval events leave agent_id empty: the requesting agent is the actor
    const requested = first("APPROVAL_REQUESTED");
    expect(requested.agent_id).toBeNull();
    expect(cuesFor(requested, agents, at(requested))).toMatchObject([{ kind: "walk", agentId: requested.actor.id, target: { place: "approval" } }]);
  });

  it("a final failure or abort flashes red; a retry does not", () => {
    const failed = first("AGENT_RUN_FAILED", (e) => (e.payload as { final: boolean }).final);
    expect(cuesFor(failed, agents, at(failed))).toEqual([{ kind: "flash", agentId: failed.agent_id, color: "red", durationMs: FLASH_MS, seq: failed.seq }]);
    const aborted = first("AGENT_RUN_ABORTED", (e) => (e.payload as { final: boolean }).final);
    expect(cuesFor(aborted, agents, at(aborted))).toMatchObject([{ kind: "flash" }]);
    const retry = first("AGENT_RUN_ABORTED", (e) => !(e.payload as { final: boolean }).final);
    expect(cuesFor(retry, agents, at(retry))).toEqual([]);
  });

  it("a new run aborts walks; a new cycle alerts the CEO's screen", () => {
    const started = as("AGENT_RUN_STARTED", { attempt: 1, task_name: "x", required_role: "researcher" });
    expect(cuesFor(started, agents, at(started))).toEqual([{ kind: "abort_walks", agentId: started.agent_id, seq: started.seq }]);
    const withCeo = { ...agents, ceo1: { ...Object.values(agents)[0], id: "ceo1", role: "ceo" } };
    const cycle = as("CYCLE_STARTED", { seq: 1, stage: "PLANNING", deadline: null });
    expect(cuesFor(cycle, withCeo, at(cycle))).toMatchObject([{ kind: "screen_alert", agentId: "ceo1" }]);
  });

  it("pose changes are not cues: the mapping shows them", () => {
    for (const type of ["AGENT_THINKING", "AGENT_WORKING", "AGENT_REVIEWING", "AGENT_WAITING", "AGENT_PAUSED", "TASK_CREATED"]) {
      const e = first(type);
      expect(cuesFor(e, agents, at(e)), type).toEqual([]);
    }
  });

  it("TTL: events older than 10 s make no cue (a reconnect replaying history)", () => {
    const done = first("AGENT_RUN_COMPLETED");
    expect(cuesFor(done, agents, at(done, CUE_TTL_MS))).toHaveLength(1);
    expect(cuesFor(done, agents, at(done, CUE_TTL_MS + 1))).toEqual([]);
    const later = new Date(Date.parse(events.at(-1)!.occurred_at) + 60_000);
    expect(events.flatMap((e) => cuesFor(e, agents, later))).toEqual([]);
  });
});

describe("cue queue (merge rules)", () => {
  const walk = (agentId: string, role: string, seq = 1): WalkCue => ({ kind: "walk", agentId, target: { role }, carry: "document", returnAfter: true, seq });
  const plan = () => 1000;

  it("the same target is not walked twice (hand-off + unlock of one completion = one walk)", () => {
    const q = new CueQueue();
    q.apply([walk("a", "analyst", 1)], 0);
    q.apply([walk("a", "analyst", 2)], 0);
    expect(q.queued("a")).toHaveLength(1);
    q.step(0, plan);
    expect(q.walk("a")?.cue.seq).toBe(1);
    q.apply([walk("a", "analyst", 3)], 10); // already walking there
    expect(q.queued("a")).toHaveLength(0);
  });

  it("walks run one after another, at most a few waiting", () => {
    const q = new CueQueue();
    q.apply(["r1", "r2", "r3", "r4", "r5", "r6"].map((role, i) => walk("a", role, i)), 0);
    expect(q.queued("a")).toHaveLength(MAX_WALKS_QUEUED);
    q.step(0, plan);
    expect(q.walk("a")?.cue.target).toEqual({ role: "r1" });
    q.step(999, plan);
    expect(q.walk("a")?.cue.target).toEqual({ role: "r1" });
    q.step(1000, plan);
    q.step(1000, plan);
    expect(q.walk("a")?.cue.target).toEqual({ role: "r2" });
  });

  it("a new run aborts the walk now and drops the waiting ones", () => {
    const q = new CueQueue();
    q.apply([walk("a", "analyst"), walk("a", "writer")], 0);
    q.step(0, plan);
    expect(q.walk("a")).toBeTruthy();
    q.apply([{ kind: "abort_walks", agentId: "a", seq: 9 }], 100);
    expect(q.walk("a")).toBeUndefined();
    expect(q.queued("a")).toHaveLength(0);
  });

  it("effects run alongside walks and restart when repeated; a long pause fast-forwards", () => {
    const q = new CueQueue();
    const flash: VisualCue = { kind: "flash", agentId: "a", color: "red", durationMs: 1000, seq: 1 };
    q.apply([walk("a", "analyst"), flash], 0);
    q.step(0, plan);
    expect(q.walk("a")).toBeTruthy();
    expect(q.effect("flash", "a")).toBeTruthy();
    q.apply([{ ...flash, seq: 2 }], 800);
    q.step(1500, plan);
    expect(q.effect("flash", "a")?.cue.seq).toBe(2); // restarted at 800, ends at 1800
    q.step(3_600_000, plan); // the tab was hidden for an hour
    expect(q.walk("a")).toBeUndefined();
    expect(q.effect("flash", "a")).toBeUndefined();
  });

  it("a walk with nowhere to go is skipped", () => {
    const q = new CueQueue();
    q.apply([walk("a", "nobody"), walk("a", "analyst")], 0);
    q.step(0, (cue) => ("role" in cue.target && cue.target.role === "nobody" ? null : 500));
    expect(q.walk("a")?.cue.target).toEqual({ role: "analyst" });
  });
});

describe("CueDirector (store -> queue)", () => {
  it("the snapshot and replayed history make no cues; fresh events do", () => {
    const store = createRealtimeStore();
    store.getState().hydrate(fixture.snapshot_before);
    let clock = 0;
    const director = new CueDirector(store, () => clock);
    store.getState().applyEvents(fixture.events); // old events: state only
    director.queue.step(0, () => 1000);
    expect(Object.keys(agents).some((id) => director.queue.walk(id) || director.queue.effect("flash", id))).toBe(false);

    // the same completion, happening now
    const now = new Date().toISOString();
    const handed = first("AGENT_RUN_COMPLETED", (e) => handoffRoles(e).length === 1);
    const done = fixture.events.find((e) => e.event_id === handed.event_id)!;
    const seq = store.getState().company!.lastSeq + 1;
    store.getState().applyEvent({ ...done, seq, event_id: "01a0b700-0000-7000-8000-000000000001", occurred_at: now });
    clock = 5;
    director.queue.step(clock, () => 1000);
    expect(director.queue.walk(done.agent_id as string)?.cue).toMatchObject({ target: { role: handoffRoles(handed)[0] }, seq });
    director.dispose();
  });
});

describe("routes", () => {
  const members = fixture.snapshot_before.agents.map((a) => ({ id: a.id, role: a.role, name: a.display_name, character: "character-male-a" as const, department: null, office_zone_key: null, business_unit: null }));
  const { seats } = assignSeats(members);
  const researcher = members.find((m) => m.role === "researcher")!;

  it("to a colleague of the target role, there and back, plus the hand-over", () => {
    const route = routeFor({ kind: "walk", agentId: researcher.id, target: { role: "analyst" }, carry: "document", returnAfter: true, seq: 1 }, { members, seats })!;
    const analystSeats = members.filter((m) => m.role === "analyst").map((m) => seats.get(m.id)!.approach);
    expect(analystSeats).toContainEqual(route.path.at(-1));
    expect(route.path[0]).toEqual(seats.get(researcher.id)!.chair);
    expect(route.durationMs).toBe(Math.round((route.length / WALK_SPEED) * 2000 + HANDOVER_MS));
  });

  it("to the approval desk; a role nobody has still has its desk; no seat, no route", () => {
    const toDesk = routeFor({ kind: "walk", agentId: researcher.id, target: { place: "approval" }, carry: "document", returnAfter: true, seq: 1 }, { members, seats })!;
    expect(toDesk.length).toBeGreaterThan(5);
    const toEditor = routeFor({ kind: "walk", agentId: researcher.id, target: { role: "editor" }, carry: "document", returnAfter: true, seq: 1 }, { members, seats });
    expect(toEditor).not.toBeNull();
    expect(routeFor({ kind: "walk", agentId: "stranger", target: { role: "analyst" }, carry: "none", returnAfter: false, seq: 1 }, { members, seats })).toBeNull();
  });
});

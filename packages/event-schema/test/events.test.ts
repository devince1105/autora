import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { EPHEMERAL_EVENT_TYPES, EVENT_TYPES, parseEvent, type EventEnvelope } from "../src";

// Written by the Python Phase 1 acceptance test from real database rows (make phase1-acceptance).
const fixture: unknown[] = JSON.parse(
  readFileSync(new URL("./fixtures/phase1-events.json", import.meta.url), "utf8"),
);

function first(): Record<string, unknown> {
  return structuredClone(fixture[0]) as Record<string, unknown>;
}

describe("events emitted by the Python runtime", () => {
  it("covers the agent walk through all activity states", () => {
    expect(fixture.length).toBeGreaterThanOrEqual(9);
  });

  it("parses every event", () => {
    for (const raw of fixture) {
      const parsed = parseEvent(raw);
      if (!parsed.ok) throw new Error(`${parsed.eventType}: ${parsed.error}`);
    }
  });

  it("narrows payloads by event_type", () => {
    const events = fixture.map((raw) => {
      const parsed = parseEvent(raw);
      if (!parsed.ok) throw new Error(parsed.error);
      return parsed.event;
    });
    const working = events.find(
      (e): e is Extract<EventEnvelope, { event_type: "AGENT_WORKING" }> =>
        e.event_type === "AGENT_WORKING",
    );
    expect(working?.payload.tool).toBe("web_search");
    expect(working?.payload.progress).toEqual({ label: "sources", current: 12, target: 40 });

    const completed = events.find((e) => e.event_type === "AGENT_RUN_COMPLETED");
    if (completed?.event_type !== "AGENT_RUN_COMPLETED") throw new Error("missing");
    expect(completed.payload.cost_usd).toBe("0.184"); // money stays a decimal string
    expect(completed.payload.handoff[0]?.to_role).toBe("analyst");

    const seqs = events.map((e) => e.seq ?? -1);
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
  });
});

describe("tolerance rules (03_EVENT_MODEL §7)", () => {
  it("ignores unknown payload fields added by a newer server", () => {
    const raw = first();
    (raw.payload as Record<string, unknown>).added_later = true;
    expect(parseEvent(raw).ok).toBe(true);
  });

  it("rejects unknown event types without throwing", () => {
    const raw = { ...first(), event_type: "AGENT_DANCING" };
    const parsed = parseEvent(raw);
    expect(parsed).toMatchObject({ ok: false, eventType: "AGENT_DANCING" });
  });

  it("rejects an unknown schema version", () => {
    expect(parseEvent({ ...first(), schema_version: 99 }).ok).toBe(false);
  });

  it("rejects malformed ids and payloads", () => {
    expect(parseEvent({ ...first(), company_id: "not-a-uuid" }).ok).toBe(false);
    expect(parseEvent({ ...first(), payload: { reason: "bogus" } }).ok).toBe(false);
    expect(parseEvent(null)).toMatchObject({ ok: false, eventType: undefined });
  });
});

describe("catalog constants", () => {
  it("lists ephemeral types as a subset of all types", () => {
    expect(EPHEMERAL_EVENT_TYPES).toEqual(new Set(["AGENT_HEARTBEAT", "AGENT_STEP_PROGRESS"]));
    for (const t of EPHEMERAL_EVENT_TYPES) expect(EVENT_TYPES).toContain(t);
  });
});

import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

import { view } from "@/realtime/reducer";

import { createRealtimeStore, serverNow } from "./realtime";

const fixture = JSON.parse(
  readFileSync(new URL("../realtime/__fixtures__/contract.json", import.meta.url), "utf8"),
) as { snapshot_before: Record<string, unknown>; events: Record<string, unknown>[]; snapshot_after: Record<string, unknown> };

describe("realtime store", () => {
  it("hydrates from a validated snapshot and rejects a malformed one", () => {
    const store = createRealtimeStore();
    expect(() => store.getState().hydrate({ ...fixture.snapshot_before, last_seq: "x" })).toThrow();
    expect(store.getState().company).toBeNull();
    store.getState().hydrate(fixture.snapshot_before);
    expect(store.getState().company?.lastSeq).toBe(fixture.snapshot_before.last_seq);
  });

  it("applies raw events in one update and reaches the same state as the Python reducer", () => {
    const store = createRealtimeStore();
    store.getState().hydrate(fixture.snapshot_before);
    const listener = vi.fn();
    store.subscribe(listener);
    expect(store.getState().applyEvents(fixture.events)).toBe(fixture.events.length);
    expect(listener).toHaveBeenCalledTimes(1);
    const company = store.getState().company;
    if (!company) throw new Error("not hydrated");
    const projection = view(company, new Date(fixture.snapshot_after.server_time as string));
    expect(projection.last_seq).toBe(fixture.snapshot_after.last_seq);
    expect(projection.tasks.length).toBe((fixture.snapshot_after.tasks as unknown[]).length);
    expect(store.getState().connection.lastEventAt).not.toBeNull();
  });

  it("drops invalid events without throwing, and replays are no-ops", () => {
    const store = createRealtimeStore();
    store.getState().hydrate(fixture.snapshot_before);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const first = fixture.events[0];
    expect(store.getState().applyEvent({ ...first, event_type: "AGENT_DANCING" })).toBe(false);
    expect(store.getState().dropped).toBe(1);
    expect(store.getState().applyEvent(first)).toBe(true);
    expect(store.getState().applyEvent(first)).toBe(false);
    warn.mockRestore();
  });

  it("ignores events before the first snapshot", () => {
    const store = createRealtimeStore();
    expect(store.getState().applyEvents(fixture.events)).toBe(0);
    expect(store.getState().company).toBeNull();
  });

  it("corrects the clock with the server offset", () => {
    const store = createRealtimeStore();
    store.getState().setConnection({ status: "live", serverOffsetMs: 60_000 });
    expect(serverNow(store.getState()).getTime() - Date.now()).toBeGreaterThanOrEqual(59_000);
    store.getState().reset();
    expect(store.getState().connection.status).toBe("idle");
  });
});

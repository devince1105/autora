import { describe, expect, it } from "vitest";

import { Latency, percentile, SAMPLE_CAP } from "./latency";

describe("handling latency (AC-S7)", () => {
  it("reports the percentiles of what it was given", () => {
    const latency = new Latency();
    for (const ms of [1, 2, 3, 4, 100]) latency.record(ms);
    const stats = latency.stats();
    expect(stats).toMatchObject({ count: 5, events: 5, max: 100 });
    expect(stats.p50).toBe(3);
    expect(stats.p95).toBe(100);
  });

  it("counts the events inside a message, not just the messages", () => {
    const latency = new Latency();
    latency.record(5, 40); // one backlog message carrying forty events
    expect(latency.stats()).toMatchObject({ count: 1, events: 40 });
  });

  it("keeps the last samples and nothing more, however long the page stays open", () => {
    const latency = new Latency();
    for (let i = 0; i < SAMPLE_CAP * 3; i++) latency.record(i % 7);
    expect(latency.stats().count).toBe(SAMPLE_CAP * 3);
    expect(latency.stats().max).toBeLessThanOrEqual(6); // the ring wrapped; nothing grew
    latency.reset();
    expect(latency.stats()).toMatchObject({ count: 0, p95: 0, max: 0 });
  });

  it("an empty percentile is zero, not a crash", () => {
    expect(percentile([], 95)).toBe(0);
    expect(new Latency().stats().p50).toBe(0);
  });
});

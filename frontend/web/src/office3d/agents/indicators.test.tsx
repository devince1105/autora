// @vitest-environment jsdom
import ReactThreeTestRenderer from "@react-three/test-renderer";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Profiler } from "react";
import { Color, type InstancedMesh, type PlaneGeometry } from "three";
import { afterEach, describe, expect, it } from "vitest";

import { createRealtimeStore, realtimeStore, serverNow } from "@/stores/realtime";

import { allSeats, assignSeats } from "../scene/layout";
import { visualForAgent, type VisualState } from "../visual/mapping";
import { CueDirector, CueProvider } from "../visual/CueRunner";
import { VisualTracker, VisualTrackerProvider } from "../visual/tracker";
import { applyTag, BLINK, lampColors, SCREEN_COLOR, screenColor } from "./indicators";
import { DeskStatus } from "./StatusIndicators";

const fixture = JSON.parse(readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8")) as {
  snapshot_before: { agents: { id: string; role: string }[] };
  events: Record<string, unknown>[];
};

afterEach(() => realtimeStore.getState().reset());

const hex = (c: Color) => `#${c.getHexString()}`;

describe("screen and lamp colours", () => {
  it("each screen state has its colour; active flickers a little, alert pulses", () => {
    expect(hex(screenColor("off", 1))).toBe(SCREEN_COLOR.off);
    expect(hex(screenColor("dim", 1))).toBe(SCREEN_COLOR.dim);
    const active = [0, 0.1, 0.2].map((t) => screenColor("active", t).r);
    expect(Math.max(...active) - Math.min(...active)).toBeLessThan(0.15);
    const peak = screenColor("alert", BLINK.blink_amber / 4).r; // sin = 1
    const trough = screenColor("alert", (3 * BLINK.blink_amber) / 4).r; // sin = -1
    expect(peak).toBeGreaterThan(trough * 2);
  });

  it("lamps: off throws no light, on is steady, amber and red blink (red twice as fast)", () => {
    expect(hex(lampColors("off", 0).glow)).toBe("#000000");
    expect(hex(lampColors("on", 0).glow)).toBe(hex(lampColors("on", 0.37).glow));
    const glow = (light: "blink_amber" | "blink_red", t: number) => lampColors(light, t).glow.r;
    expect(glow("blink_amber", BLINK.blink_amber / 4)).toBeGreaterThan(0.5);
    expect(glow("blink_amber", (3 * BLINK.blink_amber) / 4)).toBeLessThan(0.05);
    expect(BLINK.blink_red).toBe(BLINK.blink_amber / 2);
    expect(glow("blink_red", BLINK.blink_red / 4)).toBeGreaterThan(0.5);
  });
});

describe("head tag", () => {
  function tag() {
    const badge = document.createElement("span");
    const bubble = document.createElement("span");
    return { badge, bubble };
  }
  const visual = (badge: VisualState["badge"], bubble?: string): VisualState => ({ pose: "sit_idle", screen: "dim", deskLight: "on", badge, bubble });

  it("writes the badge with its tone, and the bubble", () => {
    const els = tag();
    applyTag(els, visual({ text: "工作中", tone: "active" }, "搜尋… 37/40"));
    expect(els.badge.textContent).toBe("工作中");
    expect(els.badge.className).toContain("text-ok");
    expect(els.bubble.textContent).toBe("搜尋… 37/40");
    expect(els.bubble.hidden).toBe(false);
  });

  it("a bubble that only repeats the badge is hidden; no visual state hides the badge", () => {
    const els = tag();
    applyTag(els, visual({ text: "等待審批", tone: "warn" }, "等待審批…"));
    expect(els.badge.className).toContain("text-warn");
    expect(els.bubble.hidden).toBe(true);
    applyTag(els, undefined);
    expect(els.badge.className).toContain("hidden");
  });
});

describe("VisualTracker", () => {
  it("bumps its version when a visual state changes, not for other events; re-reads the clock", () => {
    const store = createRealtimeStore();
    store.getState().hydrate(fixture.snapshot_before);
    let now = 0;
    const tracker = new VisualTracker(store, () => now);
    const v0 = tracker.tick();
    expect(tracker.tick()).toBe(v0); // nothing happened

    let versions = 0;
    let last = v0;
    for (const raw of fixture.events) {
      store.getState().applyEvent(raw);
      const v = tracker.tick();
      if (v !== last) versions++;
      last = v;
      const state = store.getState();
      for (const id of Object.keys(state.company!.agents)) {
        expect(tracker.get(id)).toEqual(visualForAgent(state.company!.agents[id], serverNow(state)) ?? undefined);
      }
    }
    expect(versions).toBeGreaterThan(5);
    expect(versions).toBeLessThan(fixture.events.length); // not every event changes what you see
    now += 1500; // a second later without events it looks again (display_until)
    expect(() => tracker.tick()).not.toThrow();
    tracker.dispose();
  });
});

describe("DeskStatus", () => {
  it("occupied desks show their agent's screen; empty desks are off; events re-render nothing", async () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    let commits = 0;
    const renderer = await ReactThreeTestRenderer.create(
      <Profiler id="desks" onRender={() => void commits++}>
        <VisualTrackerProvider>
          <DeskStatus />
        </VisualTrackerProvider>
      </Profiler>,
    );
    await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, 0.016));
    const before = commits;
    await ReactThreeTestRenderer.act(async () => {
      realtimeStore.getState().applyEvents(fixture.events);
      await renderer.advanceFrames(2, 0.016);
    });
    expect(commits).toBe(before);

    const screens = renderer.scene
      .findAll((n) => (n.instance as InstancedMesh).isInstancedMesh && (n.instance as InstancedMesh).geometry.type === "PlaneGeometry")[0]
      .instance as InstancedMesh<PlaneGeometry>;
    const seats = allSeats();
    expect(screens.count).toBe(seats.length * 2);
    const { seats: bySeatAgent } = assignSeats(fixture.snapshot_before.agents);
    const occupied = new Map([...bySeatAgent].map(([id, seat]) => [seat.key, id]));
    const state = realtimeStore.getState();
    const color = new Color();
    seats.forEach((seat, i) => {
      screens.getColorAt(i * 2, color);
      const agentId = occupied.get(seat.key);
      const screen = agentId ? visualForAgent(state.company!.agents[agentId], serverNow(state))!.screen : "off";
      if (screen === "off" || screen === "dim") expect(hex(color), seat.key).toBe(SCREEN_COLOR[screen]);
      else expect(color.r + color.g + color.b, seat.key).toBeGreaterThan(new Color(SCREEN_COLOR.dim).r * 3);
    });
    expect(occupied.size).toBe(6);
    await renderer.unmount();
  });
});

describe("cue effects and the approval desk", () => {
  it("a final failure flashes the agent's lamp red; the approval desk blinks while someone waits", async () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    const director = new CueDirector(realtimeStore);
    const renderer = await ReactThreeTestRenderer.create(
      <VisualTrackerProvider>
        <CueProvider director={director}>
          <DeskStatus />
        </CueProvider>
      </VisualTrackerProvider>,
    );
    const agentId = fixture.snapshot_before.agents[0].id;
    const { seats } = assignSeats(fixture.snapshot_before.agents);
    const lampIndex = allSeats().findIndex((s) => s.key === seats.get(agentId)!.key);
    const [, shades] = renderer.scene
      .findAll((n) => (n.instance as InstancedMesh).isInstancedMesh)
      .map((n) => n.instance as InstancedMesh);
    const shade = (i: number) => {
      const c = new Color();
      shades.getColorAt(i, c);
      return c;
    };

    // a flash, at its brightest (blink phase: a quarter period), is red
    director.queue.apply([{ kind: "flash", agentId, color: "red", durationMs: 60_000, seq: 1 }], performance.now());
    await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, BLINK.blink_red / 4));
    const red = shade(lampIndex);
    expect(red.r).toBeGreaterThan(red.g * 2);

    // someone waiting for approval: the approval desk lamp (the last one) turns amber
    const approvalLamp = allSeats().length;
    const waitingEvent = fixture.events.find((e) => e.event_type === "AGENT_WAITING" && (e.payload as { reason: string }).reason === "approval");
    expect(waitingEvent).toBeTruthy();
    await ReactThreeTestRenderer.act(async () => {
      realtimeStore.getState().applyEvents(fixture.events.slice(0, fixture.events.indexOf(waitingEvent!) + 1));
      await renderer.advanceFrames(1, 0.016);
    });
    const amber = shade(approvalLamp);
    expect(amber.r).toBeGreaterThan(amber.b * 2);
    await renderer.unmount();
    director.dispose();
  });
});


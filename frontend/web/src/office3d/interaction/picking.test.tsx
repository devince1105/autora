// @vitest-environment jsdom
import ReactThreeTestRenderer from "@react-three/test-renderer";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Box3, Group, Vector3, type OrthographicCamera } from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { realtimeStore } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { CameraRig } from "../camera/CameraRig";
import {
  CameraTween,
  clampTarget,
  DEFAULT_ORIENTATION,
  ease,
  fitZoom,
  FOCUS_FACTOR,
  FOCUS_MS,
  ROOM_BOX,
  ROOM_CENTRE,
  VIEW_DIRECTION,
} from "../camera/framing";
import { assignSeats, ROOM } from "../scene/layout";
import { agentForKey, avatarHandlers, onOfficeKey } from "./picking";

const fixture = JSON.parse(readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8")) as {
  snapshot_before: { agents: { id: string; role: string }[] };
};

afterEach(() => {
  realtimeStore.getState().reset();
  uiStore.getState().reset();
  vi.restoreAllMocks();
});

describe("framing", () => {
  it("the overview zoom fits the whole room in any canvas", () => {
    const z = fitZoom(ROOM_BOX, DEFAULT_ORIENTATION, 1400, 780);
    expect(fitZoom(ROOM_BOX, DEFAULT_ORIENTATION, 2800, 1560)).toBeCloseTo(z * 2);
    // every corner of the room projects inside the canvas at that zoom
    const view = new Vector3();
    const inverse = DEFAULT_ORIENTATION.clone().invert();
    const centre = ROOM_BOX.getCenter(new Vector3()).applyQuaternion(inverse);
    for (const x of [ROOM_BOX.min.x, ROOM_BOX.max.x])
      for (const y of [ROOM_BOX.min.y, ROOM_BOX.max.y])
        for (const zz of [ROOM_BOX.min.z, ROOM_BOX.max.z]) {
          view.set(x, y, zz).applyQuaternion(inverse);
          expect(Math.abs(view.x - centre.x) * z).toBeLessThanOrEqual(700 + 1);
          expect(Math.abs(view.y - centre.y) * z).toBeLessThanOrEqual(390 + 1);
        }
    expect(fitZoom(new Box3(new Vector3(-1, 0, -1), new Vector3(1, 1, 1)), DEFAULT_ORIENTATION, 800, 600)).toBeGreaterThan(z);
  });

  it("the target cannot leave the room; the move eases in and out", () => {
    expect(clampTarget(new Vector3(100, 9, -100)).toArray()).toEqual([ROOM.maxX, 2, ROOM.minZ]);
    expect([ease(0), ease(0.5), ease(1)]).toEqual([0, 0.5, 1]);
    expect(ease(0.25)).toBeLessThan(0.25);
  });

  it("a move takes FOCUS_MS, can follow a moving target, turns the view back, and can be cancelled", () => {
    const tween = new CameraTween();
    tween.start(
      { target: new Vector3(0, 0, 0), zoom: 10, direction: new Vector3(-1, 1, 0).normalize() },
      { target: new Vector3(10, 0, 0), zoom: 20, direction: VIEW_DIRECTION.clone() },
      0,
    );
    expect(tween.at(FOCUS_MS / 2)).toMatchObject({ zoom: 15 });
    tween.retarget(new Vector3(20, 0, 0));
    const end = tween.at(FOCUS_MS)!;
    expect(end.target.x).toBe(20);
    expect(end.direction!.angleTo(VIEW_DIRECTION)).toBeCloseTo(0);
    expect(tween.active).toBe(false);
    tween.start({ target: new Vector3(), zoom: 1 }, { target: new Vector3(1, 0, 0), zoom: 2 }, 0);
    tween.cancel();
    expect(tween.at(100)).toBeNull();
  });
});

describe("picking and keys", () => {
  it("clicking an avatar selects its agent (and stops the click reaching the floor)", () => {
    const stopPropagation = vi.fn();
    avatarHandlers("a1").onClick({ stopPropagation } as never);
    expect(uiStore.getState().selectedAgentId).toBe("a1");
    expect(stopPropagation).toHaveBeenCalled();
  });

  it("1–6 select a role's first agent; Esc clears; typing in a field is left alone", () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    const researchers = fixture.snapshot_before.agents.filter((a) => a.role === "researcher").map((a) => a.id).sort();
    expect(agentForKey("1")).toBe(researchers[0]);
    expect(agentForKey("5")).toBeNull(); // no marketing in this company
    expect(agentForKey("9")).toBeNull();

    onOfficeKey(new KeyboardEvent("keydown", { key: "1" }));
    expect(uiStore.getState().selectedAgentId).toBe(researchers[0]);
    const input = document.createElement("input");
    document.body.append(input);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    onOfficeKey({ key: "Escape", target: input } as unknown as KeyboardEvent);
    expect(uiStore.getState().selectedAgentId).toBe(researchers[0]);
    onOfficeKey(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(uiStore.getState().selectedAgentId).toBeNull();
  });
});

describe("CameraRig", () => {
  let clock = 0;
  beforeEach(() => {
    clock = 0;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
  });

  it("select -> focus and follow; the user drags -> free; clear -> back to the isometric overview", async () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    const agentId = fixture.snapshot_before.agents[0].id;
    const seat = assignSeats(fixture.snapshot_before.agents).seats.get(agentId)!;
    const avatar = new Group();
    avatar.userData = { agentId };
    avatar.position.set(seat.chair[0], 0.4, seat.chair[1]);

    const renderer = await ReactThreeTestRenderer.create(
      <>
        <primitive object={avatar} />
        <CameraRig />
      </>,
      { orthographic: true, camera: { position: [40, 46, 40], zoom: 30 } },
    );
    const frame = async (ms: number) => {
      clock += ms;
      await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, ms / 1000));
    };
    await frame(16);
    // the R3F root state: the camera and the default controls (CameraRig makes them default)
    const root = (renderer.scene.instance as unknown as { __r3f: { root: { getState: () => { camera: OrthographicCamera; controls: unknown } } } }).__r3f.root.getState();
    const cam = root.camera;
    const controls = root.controls as { target: Vector3; dispatchEvent: (e: { type: string }) => void };
    const overview = cam.zoom;
    expect(controls.target.distanceTo(ROOM_CENTRE)).toBeLessThan(0.01);

    // select: a 0.6 s move to the agent, closer
    await ReactThreeTestRenderer.act(async () => uiStore.getState().selectAgent(agentId));
    expect(uiStore.getState().cameraMode).toBe("follow");
    for (let i = 0; i < 40; i++) await frame(20);
    expect(cam.zoom).toBeCloseTo(overview * FOCUS_FACTOR, 1);
    expect(Math.hypot(controls.target.x - seat.chair[0], controls.target.z - seat.chair[1])).toBeLessThan(0.05);

    // it follows the agent when it walks
    avatar.position.set(seat.chair[0] + 3, 0, seat.chair[1] + 2);
    for (let i = 0; i < 60; i++) await frame(20);
    expect(Math.hypot(controls.target.x - avatar.position.x, controls.target.z - avatar.position.z)).toBeLessThan(0.1);

    // the user drags: the camera is theirs
    await ReactThreeTestRenderer.act(async () => controls.dispatchEvent({ type: "start" }));
    expect(uiStore.getState().cameraMode).toBe("free");
    const held = controls.target.clone();
    avatar.position.x += 3;
    for (let i = 0; i < 20; i++) await frame(20);
    expect(controls.target.distanceTo(held)).toBeLessThan(0.01);

    // clear: back to the whole room, from the default angle
    await ReactThreeTestRenderer.act(async () => uiStore.getState().selectAgent(null));
    for (let i = 0; i < 40; i++) await frame(20);
    expect(cam.zoom).toBeCloseTo(overview, 1);
    expect(controls.target.distanceTo(ROOM_CENTRE)).toBeLessThan(0.05);
    expect(cam.position.clone().sub(controls.target).normalize().angleTo(VIEW_DIRECTION)).toBeLessThan(0.02);
    await renderer.unmount();
  });
});

import { parseEvent } from "@autora/event-schema";
import ReactThreeTestRenderer from "@react-three/test-renderer";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Profiler, type ReactNode } from "react";
import { AnimationClip, Bone, Box3, Group, Quaternion, VectorKeyframeTrack, type Mesh, type Object3D } from "three";
import { afterEach, describe, expect, it, vi } from "vitest";

import { realtimeStore, serverNow } from "@/stores/realtime";

import { REQUIRED_CLIPS } from "../assets/characters";
import { assignSeats, seatsForRole } from "../scene/layout";
import { visualForAgent } from "../visual/mapping";
import { AgentAvatar, placeFor, type AvatarModel } from "./AgentAvatar";
import { AvatarController, HEAD_SCALE, upperBody } from "./AvatarController";

// A stand-in for a Kenney character: the same bone names, a 1 s clip per name the office plays.
function fakeModel(): AvatarModel {
  const scene = new Group();
  scene.name = "character";
  const bone = (name: string, parent: Object3D) => {
    const b = new Bone();
    b.name = name;
    parent.add(b);
    return b;
  };
  const root = bone("root", scene);
  const torso = bone("torso", root);
  for (const name of ["head", "arm-left", "arm-right"]) bone(name, torso);
  bone("leg-left", root);
  bone("leg-right", root);
  const clip = (name: string) => new AnimationClip(name, 1, [new VectorKeyframeTrack("root.position", [0, 1], [0, 0, 0, 0, 0.1, 0])]);
  return { scene, animations: REQUIRED_CLIPS.map(clip) };
}

vi.mock("@react-three/drei", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@react-three/drei")>()),
  useGLTF: () => fakeModel(),
}));

// A randomised real runtime history (T-302 contract fixture): six agents.
const fixture = JSON.parse(readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8")) as {
  snapshot_before: { agents: { id: string; role: string }[] };
  events: Record<string, unknown>[];
};

afterEach(() => {
  realtimeStore.getState().reset();
});

function counted(children: ReactNode, onCommit: () => void) {
  return (
    <Profiler id="probe" onRender={onCommit}>
      {children}
    </Profiler>
  );
}

const poseOf = (scene: { findAll: (f: (n: { instance: Object3D }) => boolean) => { instance: Object3D }[] }, agentId: string) => {
  const [group] = scene.findAll((n) => n.instance.userData?.agentId === agentId);
  return group.instance.children[0].userData.pose as string | undefined;
};

describe("AgentAvatar: store changes do not re-render React (T-405 AC)", () => {
  it("a hundred events later: every pose follows the store, React rendered once", async () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    const agents = fixture.snapshot_before.agents;
    const { seats } = assignSeats(agents);

    let commits = 0;
    const renderer = await ReactThreeTestRenderer.create(
      counted(
        <>
          {agents.map((a) => (
            <AgentAvatar key={a.id} agentId={a.id} seat={seats.get(a.id)!} model={fakeModel()} />
          ))}
        </>,
        () => void commits++,
      ),
    );
    await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, 0.016));
    const afterMount = commits;
    const expected = (agentId: string) => {
      const state = realtimeStore.getState();
      return visualForAgent(state.company!.agents[agentId], serverNow(state))!.pose;
    };
    const last = new Map(agents.map((a) => [a.id, poseOf(renderer.scene, a.id)]));
    let transitions = 0;

    for (const raw of fixture.events) {
      await ReactThreeTestRenderer.act(async () => {
        realtimeStore.getState().applyEvent(raw);
        await renderer.advanceFrames(1, 0.016);
      });
      for (const { id } of agents) {
        const pose = poseOf(renderer.scene, id);
        expect(pose, id).toBe(expected(id));
        if (pose !== last.get(id)) transitions++;
        last.set(id, pose);
      }
    }
    expect(fixture.events.length).toBeGreaterThan(100);
    expect(transitions).toBeGreaterThan(5); // poses really changed along the way
    expect(commits).toBe(afterMount); // …and React never rendered again
    await renderer.unmount();
  });

  it("a click target larger than the figure, never drawn (T-413)", async () => {
    const renderer = await ReactThreeTestRenderer.create(<AgentAvatar agentId="a1" seat={seatsForRole("researcher", 1)[0]} model={fakeModel()} />);
    const group = renderer.scene.findAll((n) => n.instance.userData?.agentId === "a1")[0].instance as Group;
    const hit = group.getObjectByName("hit-box") as Mesh;
    expect(hit.visible).toBe(false);
    const box = new Box3().setFromObject(hit, true);
    expect(box.max.y - box.min.y).toBeGreaterThan(0.9);
    await renderer.unmount();
  });

  it("standing (done) steps behind the chair; seated sits on it", () => {
    const [seat] = assignSeats([{ id: "a", role: "writer" }]).seats.values();
    expect(placeFor(seat, "sit_type")).toEqual([seat.chair[0], expect.any(Number), seat.chair[1]]);
    expect(placeFor(seat, "stand")[1]).toBe(0);
    expect(placeFor(seat, "stand")[2]).toBeGreaterThan(seat.chair[1]);
  });
});

describe("Agents: the roster renders only when it changes", () => {
  it("one avatar per seated agent; events do not re-render; a new agent does", async () => {
    const { Agents } = await import("./Agents");
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    let commits = 0;
    const renderer = await ReactThreeTestRenderer.create(counted(<Agents />, () => void commits++));
    await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, 0.016));
    const avatars = () => renderer.scene.findAll((n) => typeof n.instance.userData?.agentId === "string");
    expect(avatars()).toHaveLength(6);
    const before = commits;

    await ReactThreeTestRenderer.act(async () => void realtimeStore.getState().applyEvents(fixture.events));
    expect(commits).toBe(before);

    // someone new joins: AGENT_CREATED, built from a real envelope
    const template = fixture.events.find((e) => e.event_type === "AGENT_THINKING")!;
    const company = realtimeStore.getState().company!;
    const newcomer = {
      ...template,
      event_id: "01a0b6ff-0000-7000-8000-000000000001",
      seq: company.lastSeq + 1,
      event_type: "AGENT_CREATED",
      aggregate_type: "agent",
      aggregate_id: "01a0b6ff-0000-7000-8000-0000000000aa",
      agent_id: "01a0b6ff-0000-7000-8000-0000000000aa",
      task_id: null,
      run_id: null,
      workflow_run_id: null,
      payload: { role: "editor", display_name: "Eddie", capabilities: [], avatar_key: "character-female-c" },
    };
    expect(parseEvent(newcomer).ok).toBe(true);
    await ReactThreeTestRenderer.act(async () => void realtimeStore.getState().applyEvent(newcomer));
    expect(commits).toBeGreaterThan(before);
    expect(avatars()).toHaveLength(7);
    await renderer.unmount();
  });
});

describe("AvatarController", () => {
  it("each pose plays its clip; a nod when done, then idle", () => {
    const model = fakeModel();
    const controller = new AvatarController(model.scene, model.animations);
    controller.setPose("sit_type");
    controller.update(0.1);
    expect(controller.clip).toBe("sit");
    controller.setPose("stand");
    controller.update(0.1);
    expect(controller.clip).toBe("emote-yes");
    for (let i = 0; i < 15; i++) controller.update(0.1); // the 1 s gesture ends
    expect(controller.clip).toBe("idle");
    controller.setPose("sit_think");
    expect(controller.clip).toBe("sit");
    expect(model.scene.userData.pose).toBe("sit_think");
  });

  it("upper-body layer: typing reaches for the keys, reading looks down, rest pose is restored each frame", () => {
    const model = fakeModel();
    const controller = new AvatarController(model.scene, model.animations);
    const arm = model.scene.getObjectByName("arm-right")!;
    const head = model.scene.getObjectByName("head")!;
    const rest = new Quaternion();

    controller.setPose("sit_type");
    controller.update(0.05);
    expect(arm.quaternion.angleTo(rest)).toBeGreaterThan(1); // forearms forward
    const typing = arm.quaternion.clone();
    controller.update(0.05);
    expect(arm.quaternion.angleTo(typing)).toBeLessThan(0.3); // no accumulation frame to frame

    controller.setPose("sit_read");
    controller.update(0.05);
    expect(head.quaternion.angleTo(rest)).toBeCloseTo(0.32, 2);

    controller.setPose("stand");
    controller.update(0.05);
    expect(arm.quaternion.angleTo(rest)).toBeCloseTo(0);
    expect(Object.keys(upperBody("walk", 0))).toEqual([]);
  });

  it("heads are drawn smaller than the pack's, whatever the clips do (T-413)", () => {
    const model = fakeModel();
    const controller = new AvatarController(model.scene, model.animations);
    const head = model.scene.getObjectByName("head")!;
    expect(head.scale.x).toBeCloseTo(HEAD_SCALE);
    controller.setPose("sit_type");
    for (let i = 0; i < 5; i++) controller.update(0.1);
    expect([head.scale.x, head.scale.y, head.scale.z]).toEqual([HEAD_SCALE, HEAD_SCALE, HEAD_SCALE]);
  });
});

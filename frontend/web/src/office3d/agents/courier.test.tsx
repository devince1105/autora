import ReactThreeTestRenderer from "@react-three/test-renderer";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { AnimationClip, Bone, Group, VectorKeyframeTrack, type Mesh, type Object3D } from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { realtimeStore } from "@/stores/realtime";

import { REQUIRED_CLIPS } from "../assets/characters";
import { assignSeats, type Vec2 } from "../scene/layout";
import { CueDirector, CueProvider, HANDOVER_MS, routeFor, WALK_SPEED, type Route } from "../visual/CueRunner";
import { AgentAvatar, placeFor, type AvatarModel } from "./AgentAvatar";
import { along, courierState } from "./courier";

const fixture = JSON.parse(readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8")) as {
  snapshot_before: { agents: { id: string; role: string; display_name: string }[] };
  events: Record<string, unknown>[];
};
// The first completion handed to a role somebody on the roster holds: that is the one with a
// colleague to walk to. Which role it is comes from the history, which is regenerated.
const ROSTER_ROLES = new Set(fixture.snapshot_before.agents.map((a) => a.role));
const handoffRoles = (e: Record<string, unknown>) =>
  (((e.payload as { handoff?: { to_role: string }[] }).handoff ?? []) as { to_role: string }[]).map((h) => h.to_role);
const completion = fixture.events.find(
  (e) => e.event_type === "AGENT_RUN_COMPLETED" && handoffRoles(e).some((r) => ROSTER_ROLES.has(r)),
)!;
const HANDOFF_ROLE = handoffRoles(completion).find((r) => ROSTER_ROLES.has(r))!;

describe("courier timeline", () => {
  const route: Route = {
    path: [
      [0, 0],
      [0, 2],
      [4, 2],
    ],
    length: 6,
    durationMs: Math.round((6 / WALK_SPEED) * 2000 + HANDOVER_MS),
    lookAt: [5, 3],
    returnAfter: true,
  };
  const oneWay = (6 / WALK_SPEED) * 1000;

  it("walks the polyline at walking speed, facing where it goes", () => {
    expect(along(route.path, 1).position).toEqual([0, 1]);
    expect(along(route.path, 3).position).toEqual([1, 2]);
    expect(along(route.path, 3).heading).toBeCloseTo(Math.PI / 2); // +x
    expect(along(route.path, 99).position).toEqual([4, 2]);
    const s = courierState(route, (1 / WALK_SPEED) * 1000);
    expect(s).toMatchObject({ phase: "going", carrying: true });
    expect(s.position[1]).toBeCloseTo(1);
  });

  it("hands over facing the colleague (the document leaves halfway), then returns empty-handed", () => {
    const early = courierState(route, oneWay + 100);
    expect(early).toMatchObject({ phase: "handover", position: [4, 2], carrying: true });
    expect(early.heading).toBeCloseTo(Math.atan2(1, 1));
    expect(courierState(route, oneWay + HANDOVER_MS - 10).carrying).toBe(false);
    const back = courierState(route, oneWay + HANDOVER_MS + (1 / WALK_SPEED) * 1000);
    expect(back).toMatchObject({ phase: "returning", carrying: false });
    expect(back.position[0]).toBeCloseTo(3);
    expect(courierState(route, route.durationMs)).toMatchObject({ phase: "done", position: [0, 0] });
  });

  it("a one-way walk ends at the target", () => {
    const oneWayRoute = { ...route, returnAfter: false, durationMs: Math.round(oneWay + HANDOVER_MS) };
    expect(courierState(oneWayRoute, oneWayRoute.durationMs)).toMatchObject({ phase: "done", position: [4, 2] });
  });
});

// The same stand-in model as the avatar tests.
function fakeModel(): AvatarModel {
  const scene = new Group();
  const bone = (name: string, parent: Object3D) => {
    const b = new Bone();
    b.name = name;
    parent.add(b);
    return b;
  };
  const root = bone("root", scene);
  const torso = bone("torso", root);
  for (const name of ["head", "arm-left", "arm-right"]) bone(name, torso);
  const clip = (name: string) => new AnimationClip(name, 1, [new VectorKeyframeTrack("root.position", [0, 1], [0, 0, 0, 0, 0.1, 0])]);
  return { scene, animations: REQUIRED_CLIPS.map(clip) };
}

describe("Courier in the scene (store -> director -> avatar)", () => {
  let clock = 1_000;
  beforeEach(() => {
    vi.spyOn(performance, "now").mockImplementation(() => clock);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    realtimeStore.getState().reset();
  });

  it("a hand-off walks the avatar over and back; a new run sends it straight back to its chair (T-408 AC)", async () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    const done = completion;
    const agentId = done.agent_id as string;
    const members = fixture.snapshot_before.agents.map((a) => ({ id: a.id, role: a.role, name: a.display_name, character: "character-male-a" as const, department: null, office_zone_key: null, business_unit: null }));
    const { seats } = assignSeats(members);
    const seat = seats.get(agentId)!;
    const director = new CueDirector(realtimeStore);

    const renderer = await ReactThreeTestRenderer.create(
      <CueProvider director={director}>
        <AgentAvatar agentId={agentId} seat={seat} model={fakeModel()} />
      </CueProvider>,
    );
    const avatar = () => renderer.scene.findAll((n) => n.instance.userData?.agentId === agentId)[0].instance as Group;
    const pose = () => avatar().children[0].userData.pose as string;
    const paper = () => avatar().getObjectByName("paper") as Mesh;
    const frame = async (ms: number) => {
      clock += ms;
      await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, ms / 1000));
    };
    await frame(16);
    const seated = avatar().position.toArray();

    // the completion, now: a walk to the colleagues it handed to, carrying the result
    let seq = realtimeStore.getState().company!.lastSeq;
    const now = () => new Date(Date.now()).toISOString();
    await ReactThreeTestRenderer.act(async () => {
      realtimeStore.getState().applyEvent({ ...done, seq: ++seq, event_id: "01a0b710-0000-7000-8000-000000000001", occurred_at: now() });
    });
    await frame(16); // the runner starts the walk
    await frame(1500);
    expect(pose()).toBe("walk");
    expect(paper().visible).toBe(true);
    expect(avatar().position.y).toBe(0);
    expect(avatar().position.toArray()).not.toEqual(seated);

    const route = routeFor({ kind: "walk", agentId, target: { role: HANDOFF_ROLE }, carry: "document", returnAfter: true, seq }, { members, seats })!;
    await frame((route.length / WALK_SPEED) * 1000 - 1500 + HANDOVER_MS / 4);
    expect(pose()).toBe("stand"); // handing over, at the colleague's desk
    const end: Vec2 = route.path.at(-1)!;
    expect(avatar().position.x).toBeCloseTo(end[0]);
    expect(avatar().position.z).toBeCloseTo(end[1]);

    // a new run starts for this agent: the walk is abandoned, the avatar is back in its chair
    const started = fixture.events.find((e) => e.event_type === "AGENT_THINKING")!;
    await ReactThreeTestRenderer.act(async () => {
      realtimeStore.getState().applyEvent({
        ...started,
        seq: ++seq,
        event_id: "01a0b710-0000-7000-8000-000000000002",
        occurred_at: now(),
        agent_id: agentId,
        event_type: "AGENT_RUN_STARTED",
        payload: { attempt: 1, task_name: "next", required_role: seat.role },
      });
    });
    await frame(16);
    expect(director.queue.walk(agentId)).toBeUndefined();
    expect(avatar().position.toArray()).toEqual(seated);
    expect(avatar().rotation.y).toBeCloseTo(seat.facing);
    expect(paper().visible).toBe(false);
    expect(pose()).not.toBe("walk");
    expect(placeFor(seat, "sit_idle")).toEqual(seated);

    await renderer.unmount();
    director.dispose();
  });

  it("left alone, the walk ends back in the chair", async () => {
    realtimeStore.getState().hydrate(fixture.snapshot_before);
    const done = completion;
    const agentId = done.agent_id as string;
    const { seats } = assignSeats(fixture.snapshot_before.agents);
    const director = new CueDirector(realtimeStore);
    const renderer = await ReactThreeTestRenderer.create(
      <CueProvider director={director}>
        <AgentAvatar agentId={agentId} seat={seats.get(agentId)!} model={fakeModel()} />
      </CueProvider>,
    );
    const avatar = () => renderer.scene.findAll((n) => n.instance.userData?.agentId === agentId)[0].instance as Group;
    const frame = async (ms: number) => {
      clock += ms;
      await ReactThreeTestRenderer.act(async () => renderer.advanceFrames(1, ms / 1000));
    };
    await frame(16);
    const seated = avatar().position.toArray();
    await ReactThreeTestRenderer.act(async () => {
      const seq = realtimeStore.getState().company!.lastSeq + 1;
      realtimeStore.getState().applyEvent({ ...done, seq, event_id: "01a0b710-0000-7000-8000-000000000003", occurred_at: new Date().toISOString() });
    });
    await frame(16);
    await frame(2000);
    expect(avatar().position.toArray()).not.toEqual(seated);
    await frame(60_000); // long after
    await frame(16);
    expect(director.queue.walk(agentId)).toBeUndefined();
    expect(avatar().position.toArray()).toEqual(seated);
    await renderer.unmount();
    director.dispose();
  });
});

// One agent in the office (T-405, 04 §3): a clone of its character, seated at its desk, posed from
// the realtime store. The store is read with a plain subscription and in the frame loop — never
// through React state — so a burst of events re-renders nothing (05 §5). While a walk cue is
// running (T-408) the avatar itself is the courier: up, along the corridor with a document, a
// hand-over, and back; when the walk ends or is aborted (a new run) it is back in its chair.
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type { AnimationClip, Group, Mesh, Object3D } from "three";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";

import { realtimeStore, serverNow } from "@/stores/realtime";

import type { Seat } from "../scene/layout";
import { routeFor, useCues, type Route } from "../visual/CueRunner";
import { visualForAgent, type Pose } from "../visual/mapping";
import { avatarHandlers } from "../interaction/picking";
import { AvatarController } from "./AvatarController";
import { courierState } from "./courier";
import { useRoster } from "./roster";

/** Kenney's characters are 0.67 units tall; 2 makes a 1.35 m chibi whose head clears the chair back. */
export const AVATAR_SCALE = 2;
/** Seated, the body is lifted so the hips rest on the chair (seat top 0.46 m). */
export const SEAT_LIFT = 0.41;
/** Standing up (done), the avatar steps behind its chair. */
const STAND_BACK = 0.6;
/** Without events, re-read the store this often (COMPLETED turns IDLE on the clock). */
const RECHECK_MS = 1000;

export interface AvatarModel {
  scene: Object3D;
  animations: AnimationClip[];
}

export function placeFor(seat: Seat, pose: Pose): [number, number, number] {
  return pose === "stand" ? [seat.chair[0], 0, seat.chair[1] + STAND_BACK] : [seat.chair[0], SEAT_LIFT, seat.chair[1]];
}

export function AgentAvatar({ agentId, seat, model }: { agentId: string; seat: Seat; model: AvatarModel }) {
  const body = useMemo(() => {
    const copy = cloneSkinned(model.scene);
    copy.traverse((o) => {
      if ((o as Mesh).isMesh) o.castShadow = true;
    });
    return copy;
  }, [model.scene]);
  const controller = useMemo(() => new AvatarController(body, model.animations), [body, model.animations]);
  const group = useRef<Group>(null);
  const paper = useRef<Mesh>(null);
  const dirty = useRef(true);
  const nextCheck = useRef(0);
  const cues = useCues();
  const roster = useRoster();
  const latestRoster = useRef(roster);
  latestRoster.current = roster;
  const walk = useRef<{ seq: number; startedAt: number; route: Route | null } | null>(null);
  const handlers = useMemo(() => avatarHandlers(agentId), [agentId]);

  useEffect(() => realtimeStore.subscribe(() => void (dirty.current = true)), []);
  useEffect(() => () => controller.dispose(), [controller]);

  useFrame((_, dt) => {
    const now = performance.now();
    const g = group.current;

    // a walk cue in progress: the courier
    const active = cues?.queue.walk(agentId);
    if (active && (walk.current?.seq !== active.cue.seq || walk.current.startedAt !== active.startedAt)) {
      walk.current = { seq: active.cue.seq, startedAt: active.startedAt, route: routeFor(active.cue, latestRoster.current) };
    }
    const step = active && walk.current?.route ? courierState(walk.current.route, now - active.startedAt) : null;
    if (step && step.phase !== "done") {
      controller.setPose(step.phase === "handover" ? "stand" : "walk");
      g?.position.set(step.position[0], 0, step.position[1]);
      if (g) g.rotation.y = step.heading;
      if (paper.current) paper.current.visible = step.carrying;
      controller.update(Math.min(dt, 0.1));
      return;
    }
    if (walk.current) {
      // the walk is over or was aborted: back in the chair, whatever the state says next
      walk.current = null;
      dirty.current = true;
      if (paper.current) paper.current.visible = false;
      if (g) g.rotation.y = seat.facing;
      controller.pose = null;
    }

    if (dirty.current || now >= nextCheck.current) {
      dirty.current = false;
      nextCheck.current = now + RECHECK_MS;
      const state = realtimeStore.getState();
      const agent = state.company?.agents[agentId];
      const pose = (agent && visualForAgent(agent, serverNow(state))?.pose) || "sit_idle";
      if (pose !== controller.pose) {
        controller.setPose(pose);
        group.current?.position.set(...placeFor(seat, pose));
      }
    }
    controller.update(Math.min(dt, 0.1));
  });

  return (
    <group
      ref={group}
      position={placeFor(seat, "sit_idle")}
      rotation-y={seat.facing}
      scale={AVATAR_SCALE}
      userData={{ agentId }}
      {...handlers}
    >
      <primitive object={body} />
      {/* the document a courier carries, held at the chest (model units: the group is scaled) */}
      <mesh ref={paper} visible={false} position={[0, 0.3, 0.17]} rotation-x={-0.35}>
        <boxGeometry args={[0.11, 0.15, 0.01]} />
        <meshStandardMaterial color="#fbfbf7" />
      </mesh>
    </group>
  );
}

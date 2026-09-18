// One agent in the office (T-405, 04 §3): a clone of its character, seated at its desk, posed from
// the realtime store. The store is read with a plain subscription and in the frame loop — never
// through React state — so a burst of events re-renders nothing (05 §5).
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import type { AnimationClip, Group, Mesh, Object3D } from "three";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";

import { realtimeStore, serverNow } from "@/stores/realtime";

import type { Seat } from "../scene/layout";
import { visualForAgent, type Pose } from "../visual/mapping";
import { AvatarController } from "./AvatarController";

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
  const dirty = useRef(true);
  const nextCheck = useRef(0);

  useEffect(() => realtimeStore.subscribe(() => void (dirty.current = true)), []);
  useEffect(() => () => controller.dispose(), [controller]);

  useFrame((_, dt) => {
    const now = performance.now();
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
    <group ref={group} position={placeFor(seat, "sit_idle")} rotation-y={seat.facing} scale={AVATAR_SCALE} userData={{ agentId }}>
      <primitive object={body} />
    </group>
  );
}

// The camera (T-409, 04 §5–6): OrbitControls held to the isometric neighbourhood, the whole room
// framed on load and resize, and the selection driving it without React:
//   select an agent  -> a 0.6 s move to its desk, closer, then follow it (walks included);
//   enter a room     -> a 0.6 s move that frames that department's zone (T-600 batch 3);
//   clear selection  -> back to the overview;
//   the user drags   -> the move stops, the camera is theirs ("free") until the next selection.
import { OrbitControls } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, type ComponentRef } from "react";
import { Vector3, type Object3D, type OrthographicCamera } from "three";

import { uiStore } from "@/stores/ui";

import {
  AZIMUTH_SPREAD,
  CAMERA_DISTANCE,
  CameraTween,
  clampTarget,
  DEFAULT_AZIMUTH,
  DEFAULT_ORIENTATION,
  FOCUS_FACTOR,
  fitZoom,
  POLAR_RANGE,
  ROOM_BOX,
  ROOM_CENTRE,
  zoneBox,
  VIEW_DIRECTION,
  ZOOM_RANGE,
  type Framing,
} from "./framing";

type OrbitControlsImpl = ComponentRef<typeof OrbitControls>;

/** The avatar group of an agent (AgentAvatar tags it with userData.agentId). */
export function findAvatar(root: Object3D, agentId: string): Object3D | null {
  let found: Object3D | null = null;
  root.traverse((o) => {
    if (!found && o.userData?.agentId === agentId) found = o;
  });
  return found;
}

const FOCUS_HEIGHT = 0.8;

/** Below this canvas width the panel covers everything anyway: no offset. */
const INSET_MIN_WIDTH = 900;

/**
 * `insetRight`: pixels on the right that something covers while an agent is selected (the detail
 * panel); the focused agent is kept centred in the rest.
 */
export function CameraRig({ insetRight = 0 }: { insetRight?: number }) {
  const camera = useThree((s) => s.camera) as OrthographicCamera;
  const size = useThree((s) => s.size);
  const scene = useThree((s) => s.scene);
  const controls = useRef<OrbitControlsImpl>(null);
  const tween = useMemo(() => new CameraTween(), []);
  const overview = useRef(30);
  const followed = useRef<{ agentId: string; object: Object3D | null } | null>(null);
  const scratch = useMemo(() => ({ goal: new Vector3(), delta: new Vector3() }), []);

  /** The point to aim at so `at` sits centred left of the inset, at `zoom`. */
  const aimFor = (at: Vector3, zoom: number): Vector3 => {
    const aim = at.clone();
    if (insetRight > 0 && size.width >= INSET_MIN_WIDTH) {
      const right = new Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
      aim.addScaledVector(right, insetRight / 2 / zoom);
    }
    return aim;
  };
  const current = (): Framing => {
    const target = controls.current?.target.clone() ?? ROOM_CENTRE.clone();
    return { target, zoom: camera.zoom, direction: camera.position.clone().sub(target).normalize() };
  };
  /** The whole room, from the default isometric angle (whatever the user turned it to). */
  const overviewFraming = (): Framing => ({ target: ROOM_CENTRE.clone(), zoom: overview.current, direction: VIEW_DIRECTION.clone() });
  /** One department's room: its zone fills the canvas, at the angle the user is looking from. */
  const roomFraming = (zone: string): Framing => {
    const box = zoneBox(zone);
    if (!box) return overviewFraming();
    return {
      target: clampTarget(box.getCenter(new Vector3())),
      zoom: fitZoom(box, DEFAULT_ORIENTATION, size.width, size.height),
      direction: current().direction,
    };
  };

  // frame the room on mount and on every resize
  useLayoutEffect(() => {
    if (!controls.current) {
      camera.position.copy(ROOM_CENTRE).addScaledVector(VIEW_DIRECTION, CAMERA_DISTANCE);
      camera.lookAt(ROOM_CENTRE);
    }
    overview.current = fitZoom(ROOM_BOX, DEFAULT_ORIENTATION, size.width, size.height);
    const c = controls.current;
    if (c) {
      c.minZoom = overview.current * ZOOM_RANGE.min;
      c.maxZoom = overview.current * ZOOM_RANGE.max;
    }
    if (uiStore.getState().cameraMode === "overview" && !tween.active) {
      camera.zoom = overview.current;
      camera.updateProjectionMatrix();
      if (c) {
        camera.position.copy(ROOM_CENTRE).addScaledVector(camera.getWorldDirection(scratch.goal).negate(), CAMERA_DISTANCE);
        c.target.copy(ROOM_CENTRE);
        c.update();
      }
    }
  }, [camera, size.width, size.height, tween, scratch]);

  // selection and camera mode, read without React
  useEffect(
    () =>
      uiStore.subscribe((state, prev) => {
        if (state.selectedAgentId !== prev.selectedAgentId) {
          const id = state.selectedAgentId;
          if (id) {
            followed.current = { agentId: id, object: findAvatar(scene, id) };
            const object = followed.current.object;
            const zoom = overview.current * FOCUS_FACTOR;
            const target = object ? aimFor(object.getWorldPosition(new Vector3()).setY(FOCUS_HEIGHT), zoom) : ROOM_CENTRE.clone();
            // keep the viewing angle the user chose; only the target and zoom move
            tween.start(current(), { target, zoom }, performance.now());
            if (state.cameraMode !== "follow") uiStore.getState().setCameraMode("follow");
          } else {
            followed.current = null;
            tween.start(current(), overviewFraming(), performance.now());
          }
        } else if (state.focusedDepartment !== prev.focusedDepartment) {
          // entering a room frames its zone; stepping out goes back to the whole floor
          followed.current = null;
          const entered = state.focusedDepartment;
          tween.start(current(), entered ? roomFraming(entered.zone) : overviewFraming(), performance.now());
        } else if (state.cameraMode === "overview" && prev.cameraMode !== "overview") {
          followed.current = null;
          tween.start(current(), overviewFraming(), performance.now());
        }
      }),
    [scene, tween],
  );

  // the user takes the camera: stop moving it for them
  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const onStart = () => {
      tween.cancel();
      if (uiStore.getState().cameraMode !== "free") uiStore.getState().setCameraMode("free");
      followed.current = null;
    };
    c.addEventListener("start", onStart);
    return () => c.removeEventListener("start", onStart);
  }, [tween]);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    // follow: aim at the agent, wherever it walks
    const f = followed.current;
    if (f && uiStore.getState().cameraMode === "follow") {
      f.object ??= findAvatar(scene, f.agentId);
      if (f.object) {
        f.object.getWorldPosition(scratch.goal).setY(FOCUS_HEIGHT);
        scratch.goal.copy(aimFor(scratch.goal, tween.active ? overview.current * FOCUS_FACTOR : camera.zoom));
        if (tween.active) tween.retarget(scratch.goal);
        else moveTarget(c, camera, scratch.delta.copy(c.target).lerp(scratch.goal, 1 - Math.exp(-dt * 5)));
      }
    }
    const framing = tween.at(performance.now());
    if (framing) {
      moveTarget(c, camera, framing.target);
      if (framing.direction) camera.position.copy(framing.target).addScaledVector(framing.direction, CAMERA_DISTANCE);
      camera.zoom = framing.zoom;
      camera.updateProjectionMatrix();
    }
    const before = scratch.delta.copy(c.target);
    clampTarget(c.target);
    camera.position.add(before.subVectors(c.target, before));
    c.update();
  });

  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping
      dampingFactor={0.12}
      minPolarAngle={POLAR_RANGE.min}
      maxPolarAngle={POLAR_RANGE.max}
      minAzimuthAngle={DEFAULT_AZIMUTH - AZIMUTH_SPREAD}
      maxAzimuthAngle={DEFAULT_AZIMUTH + AZIMUTH_SPREAD}
      screenSpacePanning
      target={ROOM_CENTRE}
    />
  );
}

/** Move the orbit target and the camera together (keeps the viewing angle). */
function moveTarget(c: OrbitControlsImpl, camera: OrthographicCamera, target: Vector3): void {
  const dx = target.x - c.target.x;
  const dy = target.y - c.target.y;
  const dz = target.z - c.target.z;
  camera.position.x += dx;
  camera.position.y += dy;
  camera.position.z += dz;
  c.target.set(target.x, target.y, target.z);
}

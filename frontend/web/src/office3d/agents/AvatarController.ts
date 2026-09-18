// Drives one avatar (T-405): the pose's clip from the pack (crossfaded), a one-off clip when a
// pose begins (a nod when done), and the upper-body layer the pack does not have — typing,
// thinking, reading, slumping — as bone rotations on top of the sitting clip. No React: the
// component calls setPose() from a store subscription and update() every frame.
import {
  AnimationMixer,
  Euler,
  LoopOnce,
  LoopRepeat,
  Quaternion,
  type AnimationAction,
  type AnimationClip,
  type Object3D,
} from "three";

import { POSE_CLIP } from "../assets/characters";
import type { Pose } from "../visual/mapping";

const FADE = 0.3;
const BONES = ["torso", "head", "arm-left", "arm-right"] as const;
type BoneName = (typeof BONES)[number];

/** Upper-body rotations (radians, x = pitch forward, z = roll) per pose, at time t. */
export function upperBody(pose: Pose, t: number): Partial<Record<BoneName, [number, number, number]>> {
  switch (pose) {
    case "sit_type": {
      // both forearms on the keyboard, tapping out of step
      const tap = (phase: number) => Math.max(0, Math.sin(t * 14 + phase)) * 0.12;
      return {
        "arm-left": [-1.15 - tap(0), 0, 0.12],
        "arm-right": [-1.15 - tap(Math.PI), 0, -0.12],
        head: [0.12, 0, 0],
      };
    }
    case "sit_think":
      // chin on the right hand, head tilted, a slow sway
      return {
        "arm-right": [-2.2, 0, -0.35],
        head: [0.05, 0, 0.18 + Math.sin(t * 1.2) * 0.05],
        torso: [0.05, 0, 0],
      };
    case "sit_read":
      return {
        head: [0.32, 0, 0],
        "arm-left": [-0.9, 0, 0.1],
        "arm-right": [-0.9, 0, -0.1],
      };
    case "slump":
      return {
        head: [0.55, 0, 0],
        torso: [0.3, 0, 0],
        "arm-left": [0.15, 0, 0.05],
        "arm-right": [0.15, 0, -0.05],
      };
    case "sit_idle":
      // breathing
      return { torso: [Math.sin(t * 1.6) * 0.02, 0, 0] };
    default:
      return {};
  }
}

export class AvatarController {
  readonly mixer: AnimationMixer;
  /** The pose being shown; set to null to force the next setPose to apply. */
  pose: Pose | null = null;
  private readonly actions = new Map<string, AnimationAction>();
  private base: AnimationAction | null = null;
  private once: AnimationAction | null = null;
  private readonly bones = new Map<BoneName, { bone: Object3D; rest: Quaternion }>();
  private time = 0;
  private readonly offset = new Quaternion();
  private readonly euler = new Euler();

  constructor(
    private readonly root: Object3D,
    clips: readonly AnimationClip[],
  ) {
    this.mixer = new AnimationMixer(root);
    for (const clip of clips) this.actions.set(clip.name, this.mixer.clipAction(clip));
    for (const name of BONES) {
      const bone = root.getObjectByName(name);
      if (bone) this.bones.set(name, { bone, rest: bone.quaternion.clone() });
    }
    this.mixer.addEventListener("finished", (event) => {
      if (event.action === this.once && this.base) {
        this.base.reset().setEffectiveWeight(1).fadeIn(FADE).play();
        this.once.fadeOut(FADE);
        this.once = null;
      }
    });
  }

  /** Change pose: crossfade to its clip, play its one-off clip if it has one. No-op if unchanged. */
  setPose(pose: Pose): void {
    if (pose === this.pose) return;
    this.pose = pose;
    this.root.userData.pose = pose;
    const { base, once } = POSE_CLIP[pose];
    const next = this.actions.get(base) ?? null;
    if (next && next !== this.base) {
      next.reset().setLoop(LoopRepeat, Infinity).setEffectiveWeight(1).fadeIn(this.base ? FADE : 0).play();
      this.base?.fadeOut(FADE);
      this.base = next;
    }
    // a standing gesture still playing must not linger over a new (seated) pose
    if (this.once && base !== "idle") {
      this.once.fadeOut(FADE);
      this.once = null;
    }
    // a one-off gesture only where it does not fight the base pose (standing on standing)
    const gesture = once ? this.actions.get(once) : undefined;
    if (gesture && base === "idle") {
      this.once?.stop();
      gesture.reset().setLoop(LoopOnce, 1).setEffectiveWeight(1).fadeIn(FADE).play();
      gesture.clampWhenFinished = false;
      this.base?.fadeOut(FADE);
      this.once = gesture;
    }
  }

  /** Advance the clips, then lay the pose's upper body on top. */
  update(dt: number): void {
    this.time += dt;
    for (const { bone, rest } of this.bones.values()) bone.quaternion.copy(rest);
    this.mixer.update(dt);
    if (!this.pose) return;
    for (const [name, [x, y, z]] of Object.entries(upperBody(this.pose, this.time)) as [BoneName, [number, number, number]][]) {
      const entry = this.bones.get(name);
      if (!entry) continue;
      entry.bone.quaternion.multiply(this.offset.setFromEuler(this.euler.set(x, y, z)));
    }
  }

  /** Which clip currently drives the body (for tests and debugging). */
  get clip(): string | null {
    return this.once?.getClip().name ?? this.base?.getClip().name ?? null;
  }

  dispose(): void {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.root);
  }
}

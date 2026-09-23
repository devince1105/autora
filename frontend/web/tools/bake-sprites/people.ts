// The browser half of the people bake (D-027): the 3D office's own characters, posed and rendered
// into pixel sprites the way the furniture is.
//
// Every agent in the 3D office wears one of Kenney's twelve Mini Characters (``assets/characters``)
// and sits, walks and stands through the pack's own clips. The 2D board used to draw everybody as
// one hand-made figure facing the viewer — sitting at a desk that faced the other way. This poses
// each character the way the 3D office does (same scale, same smaller head, same lift onto the
// chair, same clips) and renders it from the bake's one camera, so the 2D figure is the 3D figure.
//
// Unlike furniture, a character's colours come from the pack's texture, not from the office's
// style: the 3D office keeps its people the same in every style (D-011), and so does this. So a
// pixel's "slot" is simply the texture colour it shows (``#rrggbb``), found by snapping what the
// renderer returns to the texture's own palette, and the tone pass shades it like everything else.
import {
  AnimationClip,
  AnimationMixer,
  Box3,
  Group,
  Mesh,
  MeshBasicMaterial,
  MeshLambertMaterial,
  NearestFilter,
  Scene,
  SRGBColorSpace,
  Vector3,
  type Material,
  type Object3D,
  type Texture,
} from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";

import { HEAD_SCALE } from "@/office3d/agents/AvatarController";
import { AVATAR_SCALE, SEAT_LIFT } from "@/office3d/agents/body";
import { CHARACTERS, characterUrl, type Character } from "@/office3d/assets/characters";

import { assemble, camera, ELEVATION, lights, PIXELS_PER_METRE, read, renderer, type BakedSprite } from "./render";

/** Frames of the walk cycle: four is what a pixel walk is drawn in — contact, pass, contact, pass. */
export const WALK_FRAMES = 4;

/**
 * Which way a figure faces. The camera looks from the front (+z), so ``toward`` shows the face,
 * ``away`` the back, and ``side`` the right profile; the left profile is ``side`` mirrored, which
 * the 2D board does when it draws.
 */
export const DIRECTIONS = { toward: 0, away: Math.PI, side: Math.PI / 2 } as const;
export type Direction = keyof typeof DIRECTIONS;

interface PoseSpec {
  pose: "sit" | "walk" | "stand";
  dir: Direction;
  frame: number;
  clip: string;
  /** Where in the clip, 0..1. */
  at: number;
}

/** Everything a figure is drawn doing: seated (facing their screens, away), walking, standing. */
function poses(): PoseSpec[] {
  const out: PoseSpec[] = [{ pose: "sit", dir: "away", frame: 0, clip: "sit", at: 0 }];
  for (const dir of Object.keys(DIRECTIONS) as Direction[]) {
    for (let frame = 0; frame < WALK_FRAMES; frame++) out.push({ pose: "walk", dir, frame, clip: "walk", at: frame / WALK_FRAMES });
    out.push({ pose: "stand", dir, frame: 0, clip: "idle", at: 0 });
  }
  return out;
}

export const personKey = (character: string, pose: string, dir: string, frame: number) => `${character}|${pose}|${dir}|${frame}`;

/**
 * How many colours one character may use. The pack's texture has 255, shared by all twelve; one
 * figure can show twenty-odd, and with five steps of light each that is more than a sprite's key
 * can hold — and more than a pixel figure this size should have. Ten is a pixel artist's budget
 * for a character, and it is chosen once over all of that character's poses, so a colour cannot
 * flicker from one walk frame to the next.
 */
export const COLOURS_PER_CHARACTER = 10;

type Rgb = [number, number, number];

/**
 * Median cut, keeping real colours: split the most spread-out group along its widest channel at
 * its weighted middle until there are ``k`` groups, then let each group be its most common colour.
 * Nothing is averaged into a colour the texture does not have.
 */
export function reduce(counts: ReadonlyMap<string, number>, k: number): Map<string, string> {
  const rgb = (h: string): Rgb => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  type Group = { colours: string[] };
  const spread = (g: Group) => {
    const cs = g.colours.map(rgb);
    return [0, 1, 2].map((c) => Math.max(...cs.map((x) => x[c])) - Math.min(...cs.map((x) => x[c])));
  };
  const groups: Group[] = [{ colours: [...counts.keys()] }];
  while (groups.length < k) {
    const splittable = groups.filter((g) => g.colours.length > 1);
    if (!splittable.length) break;
    const widest = splittable.reduce((a, b) => (Math.max(...spread(a)) >= Math.max(...spread(b)) ? a : b));
    const ranges = spread(widest);
    const channel = ranges.indexOf(Math.max(...ranges));
    const sorted = [...widest.colours].sort((a, b) => rgb(a)[channel] - rgb(b)[channel] || a.localeCompare(b));
    const total = sorted.reduce((n, c) => n + (counts.get(c) ?? 0), 0);
    let running = 0;
    let cut = 1;
    for (let i = 0; i < sorted.length - 1; i++) {
      running += counts.get(sorted[i]) ?? 0;
      if (running >= total / 2) {
        cut = i + 1;
        break;
      }
      cut = i + 1;
    }
    groups.splice(groups.indexOf(widest), 1, { colours: sorted.slice(0, cut) }, { colours: sorted.slice(cut) });
  }
  const map = new Map<string, string>();
  for (const group of groups) {
    const keep = group.colours.reduce((a, b) => ((counts.get(a) ?? 0) >= (counts.get(b) ?? 0) ? a : b));
    for (const colour of group.colours) map.set(colour, keep);
  }
  return map;
}

/** The texture's own colours: what a rendered pixel is snapped back to. */
function paletteOf(texture: Texture): [number, number, number][] {
  const image = texture.image as CanvasImageSource & { width: number; height: number };
  const canvas = document.createElement("canvas");
  canvas.width = image.width;
  canvas.height = image.height;
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(image, 0, 0);
  const data = ctx.getImageData(0, 0, image.width, image.height).data;
  const seen = new Map<number, [number, number, number]>();
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 128) continue;
    seen.set((data[i] << 16) | (data[i + 1] << 8) | data[i + 2], [data[i], data[i + 1], data[i + 2]]);
  }
  return [...seen.values()];
}

const hex = ([r, g, b]: readonly number[]) => `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;

function nearestColour(r: number, g: number, b: number, palette: readonly [number, number, number][]): string | null {
  let best: [number, number, number] | null = null;
  let bestGap = 18 * 18 * 3; // a little rounding on the way through the renderer, not a blend
  for (const colour of palette) {
    const gap = (colour[0] - r) ** 2 + (colour[1] - g) ** 2 + (colour[2] - b) ** 2;
    if (gap < bestGap) {
      bestGap = gap;
      best = colour;
    }
  }
  return best ? hex(best) : null;
}

/** The figure, posed: the 3D office's scale, head, lift and heading, at a point of a clip. */
function posed(model: { scene: Object3D; animations: AnimationClip[] }, spec: PoseSpec): Group {
  const body = cloneSkinned(model.scene);
  const group = new Group();
  group.add(body);
  group.scale.setScalar(AVATAR_SCALE);
  group.rotation.y = DIRECTIONS[spec.dir];
  group.position.y = spec.pose === "sit" ? SEAT_LIFT : 0;
  const clip = AnimationClip.findByName(model.animations, spec.clip);
  if (!clip) throw new Error(`no clip ${spec.clip}`);
  const mixer = new AnimationMixer(body);
  mixer.clipAction(clip).play();
  mixer.setTime(spec.at * clip.duration);
  // after the clip, as the 3D office does: a clip may key the head's scale
  body.getObjectByName("head")?.scale.setScalar(HEAD_SCALE);
  group.updateMatrixWorld(true);
  return group;
}

function meshes(root: Object3D): Mesh[] {
  const out: Mesh[] = [];
  root.traverse((o) => {
    if ((o as Mesh).isMesh) out.push(o as Mesh);
  });
  return out;
}

function repaint(root: Object3D, material: (original: Material) => Material): void {
  for (const mesh of meshes(root)) mesh.material = material(mesh.material as Material);
}

/**
 * Frame one pose exactly: the posed figure's own bounds (skinned, so the bounds of the pose, not
 * of the rest position), plus a pixel for the outline and room for the shadow at its feet.
 */
function frameFigure(group: Group, ppm: number) {
  const cam = camera(ELEVATION);
  const box = new Box3().setFromObject(group, true);
  box.expandByPoint(new Vector3(-SHADOW.rx, 0, -SHADOW.rz)).expandByPoint(new Vector3(SHADOW.rx, 0, SHADOW.rz));
  const corners: Vector3[] = [];
  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) corners.push(new Vector3(x, y, z).applyMatrix4(cam.matrixWorldInverse));
    }
  }
  const step = 1 / ppm;
  const left = Math.floor(Math.min(...corners.map((c) => c.x)) / step) * step - step;
  const right = Math.ceil(Math.max(...corners.map((c) => c.x)) / step) * step + step;
  const bottom = Math.floor(Math.min(...corners.map((c) => c.y)) / step) * step - step;
  const top = Math.ceil(Math.max(...corners.map((c) => c.y)) / step) * step + step;
  Object.assign(cam, { left, right, top, bottom });
  cam.updateProjectionMatrix();
  const width = Math.round((right - left) * ppm);
  const height = Math.round((top - bottom) * ppm);
  const toPixels = (world: Vector3) => {
    const view = world.clone().applyMatrix4(cam.matrixWorldInverse);
    return { x: Math.round((view.x - left) * ppm), y: Math.round((top - view.y) * ppm) };
  };
  return { cam, width, height, toPixels };
}

/**
 * The shadow at a figure's feet: a small oval, the way pixel figures are grounded. Not projected
 * from the body like the furniture's — a person is thin and moving, and a cast silhouette of legs
 * mid-stride reads as noise at this size. Seated figures have none of their own; the chair's
 * shadow is theirs.
 */
const SHADOW = { rx: 0.26, rz: 0.16 };

function shadowOval(width: number, height: number, centre: { x: number; y: number }, ppm: number) {
  const rx = SHADOW.rx * ppm;
  const ry = SHADOW.rz * ppm * Math.sin((ELEVATION * Math.PI) / 180);
  return (i: number) => {
    const pixel = i / 4;
    const col = pixel % width;
    const row = height - 1 - Math.floor(pixel / width); // the pixels come bottom row first
    return ((col + 0.5 - centre.x) / rx) ** 2 + ((row + 0.5 - centre.y) / ry) ** 2 <= 1;
  };
}

async function bakeCharacter(character: Character, ppm: number): Promise<BakedSprite[]> {
  const model = await new GLTFLoader().loadAsync(characterUrl(character));
  const texture = meshes(model.scene).map((m) => (m.material as MeshBasicMaterial).map).find(Boolean) ?? null;
  if (!texture) throw new Error(`${character} has no texture`);
  // the texture's own palette, read at full size: mipmaps would average neighbouring swatches
  texture.minFilter = NearestFilter;
  texture.magFilter = NearestFilter;
  texture.generateMipmaps = false;
  texture.needsUpdate = true;
  const palette = paletteOf(texture);

  // first, render every pose: which texture colour each pixel shows, and how lit it is
  const renders = poses().map((spec) => {
    const figure = posed(model, spec);
    const { cam, width, height, toPixels } = frameFigure(figure, ppm);
    const gl = renderer(width, height);

    // pass 1: which colour. Unlit, the texture as it is, read back in sRGB — the texture's space
    repaint(figure, (m) => new MeshBasicMaterial({ map: (m as MeshBasicMaterial).map }));
    const colourPixels = read(gl, new Scene().add(figure), cam, width, height, SRGBColorSpace);

    // pass 2: how lit, under the same rig as the furniture
    repaint(figure, () => new MeshLambertMaterial({ color: 0xffffff }));
    const toneScene = new Scene().add(figure);
    lights(toneScene);
    const tonePixels = read(gl, toneScene, cam, width, height);
    gl.dispose();

    const colours: (string | null)[] = [];
    for (let i = 0; i < colourPixels.length; i += 4) {
      colours.push(colourPixels[i + 3] < 128 ? null : nearestColour(colourPixels[i], colourPixels[i + 1], colourPixels[i + 2], palette));
    }
    return { spec, width, height, tonePixels, colours, origin: toPixels(new Vector3(0, 0, 0)) };
  });

  // then one small palette for the whole character, over every pose at once
  const counts = new Map<string, number>();
  for (const r of renders) for (const c of r.colours) if (c) counts.set(c, (counts.get(c) ?? 0) + 1);
  const reduced = reduce(counts, COLOURS_PER_CHARACTER);

  return renders.map(({ spec, width, height, tonePixels, colours, origin }) => {
    const seated = spec.pose === "sit";
    const inShadow = seated ? () => false : shadowOval(width, height, origin, ppm);
    return assemble({
      name: personKey(character, spec.pose, spec.dir, spec.frame),
      width,
      height,
      slotAt: (i) => {
        const c = colours[i / 4];
        return c ? (reduced.get(c) ?? c) : null;
      },
      tonePixels,
      shadowAt: inShadow,
      originX: origin.x,
      originY: origin.y,
      // a figure is sorted by where it stands
      anchor: origin.y,
      footprint: [0.5, 0.3],
    });
  });
}

export async function bakePeople(ppm: number = PIXELS_PER_METRE): Promise<{ sprites: BakedSprite[]; walkFrames: number }> {
  const sprites: BakedSprite[] = [];
  for (const character of CHARACTERS) sprites.push(...(await bakeCharacter(character, ppm)));
  return { sprites, walkFrames: WALK_FRAMES };
}

declare global {
  interface Window {
    bakePeople: typeof bakePeople;
  }
}
window.bakePeople = bakePeople;

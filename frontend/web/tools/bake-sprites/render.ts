// The browser half of the sprite bake (D-026): render one piece of the 3D office into pixels.
//
// Two passes over the same geometry, because a sprite has to carry two different things:
//
// - **which material** each pixel is (the desk's wood, the chair's frame, a leaf), rendered
//   unlit with every palette slot painted a unique probe colour. Nothing shades this pass, so a
//   pixel's colour *is* the slot it came from;
// - **how lit** each pixel is, rendered white under a fixed light rig. That is the form: the top
//   of the desk, the side in shadow, the highlight on the edge.
//
// Keeping them apart is what lets one bake follow all five office styles (D-011). The slot says
// "wood", the tone says "in shadow", and the theme decides what wood in shadow looks like. Baking
// the colours in would mean five bakes and a new one for every style added.
//
// Everything that would make this stop being pixel art is off: no antialiasing, no tone mapping,
// no filtering. One world unit is one metre, and a metre is exactly ``PIXELS_PER_METRE`` pixels,
// so the sprite lands on the 2D board's tile grid without rescaling.
import {
  AmbientLight,
  Box3,
  Color,
  DirectionalLight,
  DoubleSide,
  HemisphereLight,
  LinearSRGBColorSpace,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  MeshLambertMaterial,
  NoToneMapping,
  OrthographicCamera,
  NearestFilter,
  Scene,
  Vector3,
  WebGLRenderer,
  WebGLRenderTarget,
} from "three";

import { BAKEABLE } from "@/office3d/scene/furniture";
import { buildGeometry, type Part } from "@/office3d/scene/kit";
import { DEFAULT_THEME, THEMES, type Palette } from "@/office3d/palette";

/**
 * Pixels per metre of floor. 32, chosen from the contact sheet (``--preview``): at 16 a chair is
 * ten pixels and its sitter's stripes are two, and nothing reads; at 32 the books on a shelf do.
 * The 2D board's tile is a metre, so this is also its tile size (``fallback/tiles.ts``).
 */
export const PIXELS_PER_METRE = 32;

/**
 * How far above the horizon the camera sits, in degrees.
 *
 * 90 is straight down: a true plan view, where nothing has a front and a desk is a rectangle.
 * Lower shows more of each thing's face but squashes the floor, and the tile grid stops being
 * square. 60 keeps a metre of floor 28 pixels deep against 32 wide — close enough that the grid
 * still reads as square — while giving every piece a visible front to be lit and shaded. Chosen
 * from the contact sheet against 45 (a chair is mostly legs) and 75 (a chair is a square).
 */
export const ELEVATION = 60;

/** How many steps of light a material is allowed. A ramp, not a gradient (that is the whole point). */
export const TONES = 5;

export interface BakedSprite {
  name: string;
  w: number;
  h: number;
  /** One character per pixel; ``.`` is transparent. Each character is one (slot, tone) pair. */
  rows: string[];
  /** Where the piece's shadow falls on the floor around it: ``#`` shadow, ``.`` none. */
  shadow: string[];
  /** character -> the palette slot it came from and how lit it is (0 = darkest). */
  keys: Record<string, { slot: string; tone: number }>;
  /** The row of the piece's front-bottom edge: what the board sorts by so near covers far. */
  anchor: number;
  /** Where the piece's own origin sits inside the sprite, in pixels. */
  originX: number;
  originY: number;
  footprint: readonly [number, number];
}

// --- the probe palette --------------------------------------------------------------------------

/**
 * A palette whose every colour is a unique, recognisable value: slot *n* is painted
 * ``#nn40c0``-ish, spaced far enough apart that an 8-bit round trip through the renderer cannot
 * confuse two of them. Arrays (the books, the glow list) get one entry per element, because a
 * shelf of books is not one colour.
 */
export function probePalette(base: Palette): { palette: Palette; accent: string; slotOf: Map<string, string> } {
  const slotOf = new Map<string, string>();
  let next = 0;
  const probe = (slot: string): string => {
    const n = next++;
    if (n >= 216) throw new Error("more palette slots than probe colours");
    // a 6x6x6 grid: two probes are never closer than a fifth of the cube, so no amount of
    // 8-bit rounding or colour-space conversion on the way through the renderer can confuse them
    const channel = (shift: number) => (Math.floor(n / shift) % 6) * 51;
    const hex = `#${[channel(1), channel(6), channel(36)].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
    slotOf.set(hex, slot);
    return hex;
  };
  const walk = (value: unknown, path: string): unknown => {
    // every colour in the office palette is a hex string; anything else ("wood", a texture's
    // name) is not a colour and must come through untouched
    if (typeof value === "string") return value.startsWith("#") ? probe(path) : value;
    if (Array.isArray(value)) return value.map((item, i) => walk(item, `${path}.${i}`));
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, walk(item, path ? `${path}.${key}` : key)]));
    }
    return value;
  };
  // lighting is numbers and directions, not colours to swap: left as it is
  const palette = { ...(walk({ ...base, lighting: undefined }, "") as Palette), lighting: base.lighting };
  // not the palette's: the colour of whoever sits there, repainted per seat by the 2D board
  const accent = probe(ACCENT_SLOT);
  return { palette, accent, slotOf };
}

/** The slot a piece's user-coloured parts come back as (``BakeablePiece.parts``). */
export const ACCENT_SLOT = "accent";

// --- rendering ------------------------------------------------------------------------------------

interface Framing {
  camera: OrthographicCamera;
  width: number;
  height: number;
  originX: number;
  originY: number;
  anchor: number;
}


/**
 * Where the light comes from — the direction it travels, from upper left and a little behind,
 * the way the 3D office is lit (D-010: its shadows fall toward the viewer so they can be seen).
 * The tone pass is lit from here, and the shadow pass projects along it, so a piece's shading and
 * its shadow agree with each other and with the 3D view.
 */
const LIGHT_TRAVELS = new Vector3(0.4, -1.6, 0.28).normalize();

function camera(elevation: number): OrthographicCamera {
  const radians = (elevation * Math.PI) / 180;
  const cam = new OrthographicCamera(-1, 1, 1, -1, -100, 100);
  cam.position.set(0, Math.sin(radians), Math.cos(radians)).multiplyScalar(20);
  cam.lookAt(0, 0, 0);
  cam.updateMatrixWorld();
  return cam;
}

/** Squash the piece flat onto the floor along the light: its shadow, as geometry. */
function shadowMatrix(): Matrix4 {
  const d = LIGHT_TRAVELS;
  // p' = p - (p.y / d.y) d, row-major: x' = x - (dx/dy) y, y' = 0, z' = z - (dz/dy) y
  return new Matrix4().set(1, -d.x / d.y, 0, 0, 0, 0, 0, 0, 0, -d.z / d.y, 1, 0, 0, 0, 0, 1);
}

/**
 * Frame the piece exactly: the camera sees the piece and its shadow, plus one pixel all round for
 * the outline, and not a pixel more.
 */
function frame(parts: Part[], depth: number, elevation: number, ppm: number): Framing {
  const cam = camera(elevation);
  const geometry = buildGeometry(parts);
  geometry.computeBoundingBox();
  const box = geometry.boundingBox as Box3;
  geometry.dispose();

  const flatten = shadowMatrix();
  const corners: Vector3[] = [];
  for (const x of [box.min.x, box.max.x]) {
    for (const y of [box.min.y, box.max.y]) {
      for (const z of [box.min.z, box.max.z]) {
        corners.push(new Vector3(x, y, z).applyMatrix4(cam.matrixWorldInverse));
        corners.push(new Vector3(x, y, z).applyMatrix4(flatten).applyMatrix4(cam.matrixWorldInverse));
      }
    }
  }
  const step = 1 / ppm;
  const snapDown = (v: number) => Math.floor(v / step) * step - step; // one pixel for the outline
  const snapUp = (v: number) => Math.ceil(v / step) * step + step;
  const left = snapDown(Math.min(...corners.map((c) => c.x)));
  const right = snapUp(Math.max(...corners.map((c) => c.x)));
  const bottom = snapDown(Math.min(...corners.map((c) => c.y)));
  const top = snapUp(Math.max(...corners.map((c) => c.y)));

  cam.left = left;
  cam.right = right;
  cam.top = top;
  cam.bottom = bottom;
  cam.updateProjectionMatrix();

  const width = Math.round((right - left) * ppm);
  const height = Math.round((top - bottom) * ppm);
  const toPixels = (world: Vector3) => {
    const view = world.clone().applyMatrix4(cam.matrixWorldInverse);
    return { x: Math.round((view.x - left) * ppm), y: Math.round((top - view.y) * ppm) };
  };
  const origin = toPixels(new Vector3(0, 0, 0));
  const front = toPixels(new Vector3(0, 0, depth / 2));
  return { camera: cam, width, height, originX: origin.x, originY: origin.y, anchor: front.y };
}

function renderer(width: number, height: number): WebGLRenderer {
  const gl = new WebGLRenderer({ canvas: document.createElement("canvas"), antialias: false, alpha: true });
  gl.setPixelRatio(1);
  gl.setSize(width, height, false);
  gl.setClearColor(0x000000, 0);
  gl.toneMapping = NoToneMapping;
  gl.outputColorSpace = LinearSRGBColorSpace;
  return gl;
}

/**
 * Render into a texture and read that, rather than rendering to the canvas and reading the
 * drawing buffer — the browser is free to have cleared the canvas by the time anybody asks, and
 * an empty sprite is a very quiet way to fail. The target is linear throughout: no display curve
 * is applied, so pass one gives back the probe colour that went in and pass two gives back the
 * light itself.
 */
function read(gl: WebGLRenderer, scene: Scene, cam: OrthographicCamera, width: number, height: number): Uint8Array {
  const target = new WebGLRenderTarget(width, height, {
    minFilter: NearestFilter,
    magFilter: NearestFilter,
    colorSpace: LinearSRGBColorSpace,
  });
  gl.setRenderTarget(target);
  gl.clear();
  gl.render(scene, cam);
  const pixels = new Uint8Array(width * height * 4);
  gl.readRenderTargetPixels(target, 0, 0, width, height, pixels);
  gl.setRenderTarget(null);
  target.dispose();
  return pixels; // bottom-up, as WebGL hands it over
}

/**
 * The rig the tones are baked under. A strong key along ``LIGHT_TRAVELS`` so tops and left faces
 * are clearly the light side; a weak fill from the front so faces toward the viewer sit in the
 * middle of the ramp instead of going black; very little ambient, because ambient is what flattens
 * a ramp into one tone. Deliberately **not** the theme's own lighting — shading is form, and form
 * does not change when the office is redecorated.
 */
function lights(scene: Scene): void {
  const key = new DirectionalLight(0xffffff, 2.8);
  key.position.copy(LIGHT_TRAVELS).multiplyScalar(-10);
  const fill = new DirectionalLight(0xffffff, 0.55);
  fill.position.set(0.3, 0.6, 1);
  scene.add(key, fill, new HemisphereLight(0xffffff, 0x404040, 0.35), new AmbientLight(0xffffff, 0.08));
}

export function bakeOne(name: string, elevation: number = ELEVATION, ppm: number = PIXELS_PER_METRE): BakedSprite {
  const piece = BAKEABLE[name];
  if (!piece) throw new Error(`no bakeable piece ${name}`);
  const { palette, accent, slotOf } = probePalette(THEMES[DEFAULT_THEME].palette);
  const parts = piece.parts(palette, accent);
  const { camera: cam, width, height, originX, originY, anchor } = frame(parts, piece.footprint[1], elevation, ppm);

  const geometry = buildGeometry(parts);
  const gl = renderer(width, height);

  // pass 1: which material. Unlit, so what comes back is the probe colour that went in.
  const slotScene = new Scene();
  slotScene.add(new Mesh(geometry, new MeshBasicMaterial({ vertexColors: true })));
  const slotPixels = read(gl, slotScene, cam, width, height);

  // pass 2: how lit. White, so nothing but the light shows.
  const toneScene = new Scene();
  toneScene.add(new Mesh(geometry, new MeshLambertMaterial({ color: 0xffffff })));
  lights(toneScene);
  const tonePixels = read(gl, toneScene, cam, width, height);

  // pass 3: where its shadow falls. The same geometry flattened onto the floor along the light;
  // only its coverage matters.
  const shadowScene = new Scene();
  const flat = new Mesh(geometry, new MeshBasicMaterial({ color: 0xffffff, side: DoubleSide }));
  flat.matrixAutoUpdate = false;
  flat.matrix.copy(shadowMatrix());
  shadowScene.add(flat);
  const shadowPixels = read(gl, shadowScene, cam, width, height);

  gl.dispose();
  geometry.dispose();

  return assemble({
    name, width, height, slotPixels, tonePixels, shadowPixels, slotOf,
    originX, originY, anchor, footprint: piece.footprint,
  }); // prettier-ignore
}

// --- pixels -> characters -------------------------------------------------------------------------

const ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-*/<>=%$#@!?&~^|[]{}():;,_'`";

/** The tone an outline pixel gets: one below the darkest step of the ramp (``art/theme.ts``). */
export const OUTLINE_TONE = -1;

/**
 * Which slot a rendered pixel came from.
 *
 * The comparison is done where the pixels are — in linear light, the space the render target
 * holds — rather than trying to undo the renderer's colour maths and match hex strings. The probe
 * colours sit on a coarse grid, so the nearest one is never a close call; anything that is not
 * near one at all is an edge pixel blended with the background and belongs to nothing.
 */
function slotTable(slotOf: Map<string, string>): { rgb: [number, number, number]; slot: string }[] {
  const colour = new Color();
  return [...slotOf].map(([hex, slot]) => {
    colour.set(hex); // hex is sRGB; Color holds linear, which is what the target gives back
    return { rgb: [colour.r * 255, colour.g * 255, colour.b * 255] as [number, number, number], slot };
  });
}

function nearestSlot(r: number, g: number, b: number, table: ReturnType<typeof slotTable>): string | null {
  let best: string | null = null;
  let bestGap = 24 * 24; // further than this and it is not a probe
  for (const entry of table) {
    const gap = (entry.rgb[0] - r) ** 2 + (entry.rgb[1] - g) ** 2 + (entry.rgb[2] - b) ** 2;
    if (gap < bestGap) {
      bestGap = gap;
      best = entry.slot;
    }
  }
  return best;
}

interface Assembly {
  name: string;
  width: number;
  height: number;
  slotPixels: Uint8Array;
  tonePixels: Uint8Array;
  shadowPixels: Uint8Array;
  slotOf: Map<string, string>;
  originX: number;
  originY: number;
  anchor: number;
  footprint: readonly [number, number];
}

function assemble(a: Assembly): BakedSprite {
  const keys: Record<string, { slot: string; tone: number }> = {};
  const keyOf = new Map<string, string>();
  const keyFor = (slot: string, tone: number): string => {
    const id = `${slot}@${tone}`;
    let key = keyOf.get(id);
    if (key === undefined) {
      key = ALPHABET[keyOf.size];
      if (key === undefined) throw new Error(`${a.name}: more than ${ALPHABET.length} material/tone pairs`);
      keyOf.set(id, key);
      keys[key] = { slot, tone };
    }
    return key;
  };
  const table = slotTable(a.slotOf);

  // what each pixel is: a slot, or nothing
  const slots: (string | null)[][] = [];
  const grid: string[][] = [];
  const shadow: boolean[][] = [];
  for (let row = 0; row < a.height; row++) {
    const y = a.height - 1 - row; // WebGL hands back the bottom row first
    const slotRow: (string | null)[] = [];
    const line: string[] = [];
    const shadowRow: boolean[] = [];
    for (let col = 0; col < a.width; col++) {
      const i = (y * a.width + col) * 4;
      shadowRow.push(a.shadowPixels[i + 3] >= 128);
      const slot = a.slotPixels[i + 3] < 128 ? null : nearestSlot(a.slotPixels[i], a.slotPixels[i + 1], a.slotPixels[i + 2], table);
      slotRow.push(slot);
      if (slot === null) {
        line.push(".");
        continue;
      }
      // the target is linear; a ramp with even steps has to be chosen in the space eyes see in
      const linear = (a.tonePixels[i] * 0.2126 + a.tonePixels[i + 1] * 0.7152 + a.tonePixels[i + 2] * 0.0722) / 255;
      const lit = linear <= 0.0031308 ? linear * 12.92 : 1.055 * linear ** (1 / 2.4) - 0.055;
      line.push(keyFor(slot, Math.min(TONES - 1, Math.max(0, Math.round(lit * (TONES - 1))))));
    }
    slots.push(slotRow);
    grid.push(line);
    shadow.push(shadowRow);
  }

  // the outline: every empty pixel touching the piece takes the darkest tone of what it touches
  // ("selective outline") — dark brown round wood, dark green round leaves, and in the neon style a
  // glow colour stays a glow colour. A single black line round everything reads as a sticker.
  const outlined = grid.map((line) => [...line]);
  for (let row = 0; row < a.height; row++) {
    for (let col = 0; col < a.width; col++) {
      if (slots[row][col] !== null) continue;
      const touching = [
        slots[row + 1]?.[col], // below first: the base of a thing is where an outline weighs most
        slots[row][col + 1],
        slots[row][col - 1],
        slots[row - 1]?.[col],
      ].find((slot): slot is string => typeof slot === "string");
      if (touching) outlined[row][col] = keyFor(touching, OUTLINE_TONE);
    }
  }

  return {
    name: a.name,
    w: a.width,
    h: a.height,
    rows: outlined.map((line) => line.join("")),
    shadow: shadow.map((line, row) => line.map((on, col) => (on && outlined[row][col] === "." ? "#" : ".")).join("")),
    keys,
    anchor: a.anchor,
    originX: a.originX,
    originY: a.originY,
    footprint: a.footprint,
  };
}

export function bakeAll(): BakedSprite[] {
  return Object.keys(BAKEABLE).map((name) => bakeOne(name));
}

declare global {
  interface Window {
    bakeAll: typeof bakeAll;
  }
}
window.bakeAll = bakeAll;

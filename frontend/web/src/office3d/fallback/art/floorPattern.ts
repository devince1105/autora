// The floor's texture on the 2D board (D-027): planks, tiles, stone, carpet and the neon grid.
//
// The 3D office paints its floors with textures drawn in code (``scene/textures.ts``), and which
// one a floor gets — and how big — is part of the style: the open plan is planks of 1.2 m in
// 日式無印, slabs of 1.6 m in 工業風, a glowing grid in 電光風. A bake is one set of pictures for
// every style, so it cannot hold them. Instead the pattern is laid when the backdrop is painted in
// a style: every pixel the bake says is floor knows which floor it is (its palette slot) and where
// it is in the office (a metre is 32 pixels across and 28 deep), so the joints fall in the same
// places as in 3D.
//
// Pixel art rules still: a joint is one pixel, a plank is one flat colour, a speckle is one pixel.
// Nothing here is a gradient; each touch is a small fixed step darker or lighter, or a line in a
// colour of its own (grout, the grid's neon).
import type { FloorKind, FloorLook, Palette as OfficePalette } from "@/office3d/palette";

/** What the pattern does to one pixel of floor. */
export type Touch = { dark: number } | { light: number } | { colour: string; alpha?: number } | null;

/** World metres at a pixel, and at the pixels to its left and above: joints are where they differ. */
export interface FloorPixel {
  col: number;
  row: number;
  here: { x: number; z: number };
  left: { x: number; z: number };
  up: { x: number; z: number };
}

/** The grout the 3D office's tiles use (``floorTexture``). */
const GROUT = "#d6d8db";

/** A stable pseudo-random number in [0, 1) for a pair of integers: the same floor on every load. */
function hash(a: number, b = 0): number {
  let h = (Math.imul(a | 0, 374761393) + Math.imul(b | 0, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

const crossed = (a: number, b: number, step: number) => Math.floor(a / step) !== Math.floor(b / step);

/** What a floor's texture does to the pixel at ``p``. Null: leave it as the bake painted it. */
export function touch(look: FloorLook, p: FloorPixel): Touch {
  return touchAt(look, p.here.x, p.left.x, p.here.z, p.up.z, p.col, p.row);
}

/**
 * The same, on plain numbers: the world position of this pixel (``x``, ``z``), of the pixel to its
 * left (``xl``) and of the one above (``zu``). The backdrop is half a million pixels; building
 * three little objects for each of them was most of what laying a floor cost.
 */
function touchAt(look: FloorLook, x: number, xl: number, z: number, zu: number, col: number, row: number): Touch {
  const metres = look.metres ?? 1;
  switch (look.texture) {
    case "wood": {
      // four rows of planks to a texture tile, running across; their ends staggered row by row.
      // The joints are light: a plank row is only eight pixels deep, and a joint as dark as the 3D
      // texture's (2 px in 64) at one pixel in eight turns a floor into brickwork
      const rowDepth = metres / 4;
      if (crossed(z, zu, rowDepth)) return { dark: 0.16 };
      const row = Math.floor(z / rowDepth);
      const length = metres * (0.55 + 0.35 * hash(row, 1));
      const offset = hash(row, 2) * length;
      if (crossed(x - offset, xl - offset, length)) return { dark: 0.12 };
      const plank = hash(row, Math.floor((x - offset) / length));
      if (plank < 0.3) return { dark: 0.07 };
      if (plank > 0.82) return { light: 0.06 };
      return null;
    }
    case "tile": {
      // two by two to a texture tile, with the grout the 3D tiles have
      const size = metres / 2;
      if (crossed(x, xl, size) || crossed(z, zu, size)) return { colour: GROUT };
      return null;
    }
    case "stone": {
      // large slabs a shade apart, with thin light joints
      const size = metres / 2;
      if (crossed(x, xl, size) || crossed(z, zu, size)) return { light: 0.4 };
      const slab = hash(Math.floor(x / size), Math.floor(z / size) + 7);
      if (slab < 0.34) return { dark: 0.06 };
      if (slab > 0.8) return { dark: 0.03 };
      return null;
    }
    case "carpet": {
      // carpet tiles with faint seams, and a speckle in the pile
      const size = metres / 2;
      if (crossed(x, xl, size) || crossed(z, zu, size)) return { dark: 0.1 };
      const speck = hash(col, row + 31);
      if (speck < 0.05) return { dark: 0.12 };
      if (speck > 0.96) return { light: 0.08 };
      return null;
    }
    case "grid": {
      // a glowing line on every metre, a fainter one through each middle
      const lines = look.lines ?? "#19e6ff";
      if (crossed(x, xl, metres) || crossed(z, zu, metres)) return { colour: lines };
      const half = metres / 2;
      if (crossed(x + half, xl + half, metres) || crossed(z + half, zu + half, metres)) {
        return { colour: lines, alpha: 0.35 };
      }
      return null;
    }
    default:
      return null;
  }
}

/**
 * Which floor a baked pixel's slot is, in this style — or null when it is not floor.
 *
 * A zone's neon trim is floor too in a style without the trim (it was painted as the floor under
 * it, ``art/theme.ts``), so it takes the floor's pattern rather than showing as a bare line.
 */
export function floorKindOf(slot: string, palette: OfficePalette): FloorKind | null {
  const floor = /^floors\.(\w+)\.color$/.exec(slot);
  if (floor && floor[1] in palette.floors) return floor[1] as FloorKind;
  const trim = /^zoneTrim\.(\w+)$/.exec(slot);
  if (trim && !palette.zoneTrim?.[trim[1] as keyof NonNullable<OfficePalette["zoneTrim"]>] && trim[1] in palette.floors) {
    return trim[1] as FloorKind;
  }
  return null;
}

type Rgb = [number, number, number];

function channels(hex: string): Rgb {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** A pixel's colour after a touch: a fixed step toward dark or light, or toward a colour. */
export function applyTouch(rgb: Rgb, t: Exclude<Touch, null>): Rgb {
  if ("dark" in t) return rgb.map((c) => Math.round(c * (1 - t.dark))) as Rgb;
  if ("light" in t) return rgb.map((c) => Math.round(c + (255 - c) * t.light)) as Rgb;
  const target = channels(t.colour);
  const a = t.alpha ?? 1;
  return rgb.map((c, i) => Math.round(c + (target[i] - c) * a)) as Rgb;
}

/** The part of a baked piece the pattern needs: its rows, its key, and where the world's origin is. */
export interface PatternSource {
  w: number;
  h: number;
  rows: readonly string[];
  keys: Record<string, readonly [string, number]>;
  origin: readonly [number, number];
}

/**
 * Lay the floor's texture over a painted backdrop, in place. Only pixels the bake says are floor
 * are touched — walls, windows and rims keep their colours — and only floors whose look in this
 * style has a texture. ``perMetre`` is pixels per metre across and deep (32 × 28).
 */
export function patternFloors(
  image: { data: Uint8ClampedArray; width: number; height: number },
  source: PatternSource,
  palette: OfficePalette,
  perMetre: { across: number; deep: number },
): number {
  const [ox, oy] = source.origin;
  // where each column and row is in the office, worked out once rather than per pixel; index
  // ``c + 1`` so the column to the left of the first is there too
  const xs = new Float64Array(source.w + 1);
  for (let c = -1; c < source.w; c++) xs[c + 1] = (c - ox) / perMetre.across;
  // which floor each character is, by its code: an outline is not floor, nor a floor without a texture
  const lookOf: (FloorLook | null)[] = [];
  for (const [key, [slot, tone]] of Object.entries(source.keys)) {
    const kind = tone < 0 ? null : floorKindOf(slot, palette);
    const look = kind ? palette.floors[kind] : null;
    lookOf[key.charCodeAt(0)] = look?.texture ? look : null;
  }
  const data = image.data;
  let touched = 0;
  for (let row = 0; row < source.h; row++) {
    const line = source.rows[row];
    const z = (row - oy) / perMetre.deep;
    const zu = (row - 1 - oy) / perMetre.deep;
    for (let col = 0; col < source.w; col++) {
      const look = lookOf[line.charCodeAt(col)];
      if (!look) continue;
      const t = touchAt(look, xs[col + 1], xs[col], z, zu, col, row);
      if (!t) continue;
      const i = (row * image.width + col) * 4;
      if ("dark" in t) {
        const k = 1 - t.dark;
        data[i] = Math.round(data[i] * k);
        data[i + 1] = Math.round(data[i + 1] * k);
        data[i + 2] = Math.round(data[i + 2] * k);
      } else if ("light" in t) {
        data[i] = Math.round(data[i] + (255 - data[i]) * t.light);
        data[i + 1] = Math.round(data[i + 1] + (255 - data[i + 1]) * t.light);
        data[i + 2] = Math.round(data[i + 2] + (255 - data[i + 2]) * t.light);
      } else {
        const next = applyTouch([data[i], data[i + 1], data[i + 2]], t);
        data[i] = next[0];
        data[i + 1] = next[1];
        data[i + 2] = next[2];
      }
      touched++;
    }
  }
  return touched;
}

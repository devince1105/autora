// Painting a baked sprite in one of the office's styles (D-026, D-011).
//
// A baked sprite does not know any colours. Each of its characters says two things — which
// palette slot the pixel belongs to (the desk top, the chair, a leaf) and how lit it is — and this
// module answers "what colour is that, in this style?" by looking the slot up in the style's
// palette and stepping it along a light ramp. Switching the 3D office from 日式無印 to 電光風
// repaints the 2D board with it, without a second bake and without a second set of pictures.
//
// The ramp is where the pixel-art look comes from: a handful of discrete tones per material, with
// the shadows pushed a little cooler and the highlights a little warmer, the way a pixel artist
// builds a ramp by hand — rather than one colour darkened and lightened, which reads as grey mud.
import type { Palette as OfficePalette } from "@/office3d/palette";

import type { BakedPiece } from "./pieces";
import { sprite, type Palette, type Sprite } from "./sprites";

/** The slot the bake reports for a piece's user colour (a chair's stripes: its sitter's role). */
export const ACCENT_SLOT = "accent";

/** Brightness at each step of the light ramp, darkest first. The bake uses the same count. */
export const RAMP = [0.5, 0.68, 0.84, 1, 1.13] as const;

/** How far each step leans: negative toward blue (shadow), positive toward yellow (light). */
const LEAN = [-0.1, -0.05, 0, 0.03, 0.06] as const;

/**
 * What a slot is painted instead, in a style that does not have it.
 *
 * Parts that only some styles have (the neon trims round each zone, the glowing desk edge) are
 * baked always — see ``everySlot`` in the bake. In a style without them, the pixel takes the
 * colour of what the part lies on, so the trim becomes floor and the edge becomes desk: no hole,
 * no second bake.
 */
function fallbackOf(slot: string): string | undefined {
  if (slot === "deskEdge") return "deskTop";
  const trim = /^zoneTrim\.(.+)$/.exec(slot);
  if (trim) return `floors.${trim[1]}.color`;
  return undefined;
}

function lookUp(palette: OfficePalette, slot: string): string | undefined {
  let value: unknown = palette;
  for (const step of slot.split(".")) {
    if (value === null || typeof value !== "object") return undefined;
    value = (value as Record<string, unknown>)[step];
  }
  return typeof value === "string" ? value : undefined;
}

/** A slot is a path into the style's palette: ``deskTop``, ``books.3``, ``floors.base.color``. */
export function slotColour(palette: OfficePalette, slot: string, accent?: string): string | undefined {
  if (slot === ACCENT_SLOT) return accent;
  // already a colour: a person's clothes come from their character's texture, not the style
  if (/^#[0-9a-f]{6}$/i.test(slot)) return slot;
  const direct = lookUp(palette, slot);
  if (direct !== undefined) return direct;
  const instead = fallbackOf(slot);
  return instead ? lookUp(palette, instead) : undefined;
}

function channels(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const toHex = (rgb: number[]) =>
  `#${rgb.map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, "0")).join("")}`;

/** The tone the bake gives outline pixels: below the bottom of the ramp. */
export const OUTLINE_TONE = -1;

/** How dark an outline is against its material's darkest step, and how far it leans cool. */
const OUTLINE = { light: 0.36, lean: -0.04 } as const;

/** One step of the ramp: brighter or darker, and a little warmer or cooler with it. */
export function shade(hex: string, tone: number): string {
  const rgb = channels(hex);
  if (!rgb) return hex;
  if (tone <= OUTLINE_TONE) {
    const lean = OUTLINE.lean * 255;
    const [r, g, b] = rgb.map((c) => c * OUTLINE.light);
    return toHex([r + lean * 0.6, g + lean * 0.3, b - lean]);
  }
  const step = Math.min(RAMP.length - 1, Math.max(0, Math.round(tone)));
  const light = RAMP[step];
  const lean = LEAN[step] * 255;
  const [r, g, b] = rgb.map((c) => c * light);
  return toHex([r + lean * 0.6, g + lean * 0.3, b - lean]);
}

/**
 * The sprite ``drawSprite`` wants: the baked rows, with a palette that maps each character to its
 * colour in this style. Cheap enough to redo whenever the style changes — a few dozen characters
 * per piece, not a pixel loop.
 */
export function paint(piece: BakedPiece, palette: OfficePalette, accent?: string): Sprite {
  const rim = neonRim(palette);
  const colours: Palette = {};
  for (const [key, [slot, tone]] of Object.entries(piece.keys)) {
    const base = slotColour(palette, slot, accent);
    if (!base) continue;
    colours[key] = tone <= OUTLINE_TONE && rim ? rim : shade(base, tone);
  }
  return sprite(piece.rows, colours, piece.anchor);
}

/**
 * The outline colour of a style that glows, or null for the rest.
 *
 * In a dark neon office (電光風) a dark outline on a dark floor is no outline at all, and the 3D
 * view of that style is read by its glowing edges anyway — so there the outline *is* the neon:
 * the building's own edge colour (the outer wall's top), a little dimmed so it rims a thing
 * rather than shouting over it.
 */
export function neonRim(palette: OfficePalette): string | null {
  if (!palette.glow.length) return null;
  const rgb = channels(palette.wallCap);
  return rgb ? toHex(rgb.map((c) => c * 0.8)) : null;
}

/**
 * The piece's shadow on the floor, as a sprite of one colour. Drawn first and translucent, so the
 * floor shows through it: a shadow darkens what it falls on rather than painting over it.
 */
export function paintShadow(piece: BakedPiece, colour = "#000000"): Sprite | null {
  if (!piece.shadow.some((line) => line.includes("#"))) return null;
  return sprite(piece.shadow, { "#": colour }, piece.anchor);
}

/** How dark shadows are: the alpha they are drawn at. */
export const SHADOW_ALPHA = 0.22;

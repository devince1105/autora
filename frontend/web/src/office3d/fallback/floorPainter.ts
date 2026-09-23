// Painting one frame of the 2D floor (D-026, D-027).
//
// Kept apart from the React component so the same code paints the page and the bake tool's
// preview: what the preview shows is what the page draws, not a copy of it that could drift.
//
// Order: the backdrop, then every shadow (they lie on the floor, under everything), then the
// things that stand — furniture, walls and people — back to front by their feet, so a person in
// front of a desk covers it and one behind it does not. Last, a veil over every room but the
// chosen one.
import { THEMES, type ThemeId } from "../palette";
import * as atlas from "./art/atlas";
import { BACKDROP_KEY, piece } from "./art/pieces";
import { drawSprite } from "./art/sprites";
import { paint, paintShadow, SHADOW_ALPHA } from "./art/theme";
import { CONSOLE } from "./console";
import { NPC, PERSON_SCALE, worldToPixels, type Scene } from "./tiles";

/** How dark the veil over the rooms that are not chosen is. */
const VEIL = "rgba(4, 10, 9, 0.55)";
/**
 * How far above a room's floor its things reach, so the veil's hole takes them in: a walled room's
 * shelves and glass front stand tall, an open zone's tallest things are its monitors. Too much
 * and a strip of empty corridor is lit with the room.
 */
const ROOM_HEIGHT_PX = { walled: 48, open: 22 } as const;

/**
 * Painted pictures, one per piece, style and accent, kept as canvases.
 *
 * A baked piece is characters and a key; painting it is a loop over its runs. Doing that every
 * frame for an 810 × 522 backdrop would be thousands of fills a frame, so each picture is painted
 * once, into its own canvas, and the frame only copies canvases. A new style is a new set.
 */
export class Pictures {
  private readonly cache = new Map<string, HTMLCanvasElement | null>();

  constructor(
    readonly theme: ThemeId,
    private readonly makeCanvas: () => HTMLCanvasElement = () => document.createElement("canvas"),
  ) {}

  private canvasOf(key: string, make: (ctx: CanvasRenderingContext2D) => void, w: number, h: number) {
    if (this.cache.has(key)) return this.cache.get(key) ?? null;
    const canvas = this.makeCanvas();
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      this.cache.set(key, null);
      return null;
    }
    make(ctx);
    this.cache.set(key, canvas);
    return canvas;
  }

  art(bake: string, accent?: string): HTMLCanvasElement | null {
    const found = piece(bake);
    if (!found) return null;
    const art = paint(found, THEMES[this.theme].palette, accent);
    return this.canvasOf(`art|${bake}|${accent ?? ""}`, (ctx) => drawSprite(ctx, art, 0, 0), found.w, found.h);
  }

  shadow(bake: string): HTMLCanvasElement | null {
    const found = piece(bake);
    const art = found ? paintShadow(found) : null;
    if (!found || !art) return null;
    return this.canvasOf(`shadow|${bake}`, (ctx) => drawSprite(ctx, art, 0, 0), found.w, found.h);
  }
}

/** Somebody walking between desks, where they are this frame. */
export interface WalkerNow {
  agentId: string;
  /** World metres. */
  position: readonly [number, number];
  carrying?: boolean;
}

export interface FrameOptions {
  selected: string | null;
  focused: string | null;
  /** Who is out of their chair and where; they are drawn walking, not seated. */
  walking: readonly WalkerNow[];
  /** Colour by agent, for the ones walking. */
  colours: ReadonlyMap<string, string>;
  /** Milliseconds, for the walking frame. */
  time: number;
}

/** A person in their role's colours, with a frame for whoever is selected. */
export function paintPerson(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  options: { step?: 0 | 1; carrying?: boolean; selected?: boolean } = {},
) {
  const { step = 0, carrying, selected } = options;
  if (selected) {
    ctx.fillStyle = CONSOLE.accent;
    ctx.fillRect(x - 2, y - 2, NPC.w + 4, 2);
    ctx.fillRect(x - 2, y + NPC.h, NPC.w + 4, 2);
    ctx.fillRect(x - 2, y, 2, NPC.h);
    ctx.fillRect(x + NPC.w, y, 2, NPC.h);
  }
  drawSprite(ctx, atlas.PERSON[step], x, y, { recolor: { S: color, H: shadeOf(color) }, scale: PERSON_SCALE });
  if (carrying) drawSprite(ctx, atlas.DOCUMENT, x + NPC.w - 4, y + 16, { scale: PERSON_SCALE });
}

/** A darker step of a role's colour, for hair against the shirt. */
function shadeOf(color: string): string {
  const value = Number.parseInt(color.replace("#", ""), 16);
  if (Number.isNaN(value)) return "#2a1d16";
  const dark = [(value >> 16) & 255, (value >> 8) & 255, value & 255].map((c) => Math.round(c * 0.45));
  return `#${dark.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** Darken every room but the chosen one; nothing when the whole floor is shown. */
function veil(ctx: CanvasRenderingContext2D, scene: Scene, focused: string | null) {
  const room = focused ? scene.rooms.find((r) => r.id === focused) : undefined;
  if (!room) return;
  ctx.save();
  ctx.fillStyle = VEIL;
  ctx.beginPath();
  ctx.rect(0, 0, scene.width, scene.height);
  const rise = ROOM_HEIGHT_PX[room.kind];
  ctx.rect(room.x, room.y - rise, room.width, room.height + rise);
  ctx.fill("evenodd");
  ctx.restore();
}

/** One frame of the floor. The canvas is expected to be ``scene.width`` × ``scene.height``. */
export function paintFloor(ctx: CanvasRenderingContext2D, scene: Scene, pictures: Pictures, frame: FrameOptions): void {
  const { selected, focused, walking, colours, time } = frame;
  const moving = new Set(walking.map((walker) => walker.agentId));

  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = CONSOLE.bg;
  ctx.fillRect(0, 0, scene.width, scene.height);

  const backdrop = pictures.art(BACKDROP_KEY);
  if (backdrop) ctx.drawImage(backdrop, scene.backdrop.x, scene.backdrop.y);

  // shadows lie on the floor: all of them before anything that stands
  ctx.globalAlpha = SHADOW_ALPHA;
  for (const prop of scene.props) {
    const shadow = pictures.shadow(prop.bake);
    if (shadow) ctx.drawImage(shadow, prop.x, prop.y);
  }
  ctx.globalAlpha = 1;

  // everything that stands on the floor, painted back to front by its feet
  const standing: { foot: number; paint: () => void }[] = [];
  for (const prop of scene.props) {
    const picture = pictures.art(prop.bake, prop.accent);
    if (picture) standing.push({ foot: prop.foot, paint: () => ctx.drawImage(picture, prop.x, prop.y) });
  }
  for (const npc of scene.npcs) {
    if (moving.has(npc.agentId)) continue; // out of their chair
    standing.push({ foot: npc.foot, paint: () => paintPerson(ctx, npc.x, npc.y, npc.color, { selected: npc.agentId === selected }) });
  }
  for (const walker of walking) {
    const colour = colours.get(walker.agentId);
    if (!colour) continue;
    const spot = worldToPixels(walker.position[0], walker.position[1]);
    const x = Math.round(spot.x - NPC.w / 2);
    const y = Math.round(spot.y - NPC.h + 10);
    standing.push({
      foot: spot.y,
      paint: () =>
        paintPerson(ctx, x, y, colour, {
          step: Math.floor(time / 170) % 2 === 0 ? 0 : 1,
          carrying: walker.carrying,
          selected: walker.agentId === selected,
        }),
    });
  }
  standing.sort((left, right) => left.foot - right.foot);
  for (const item of standing) item.paint();

  veil(ctx, scene, focused);
}

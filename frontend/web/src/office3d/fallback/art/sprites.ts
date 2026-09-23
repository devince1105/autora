// The pixel-art engine for the 2D office (T-410 stage 4).
//
// A sprite is written as rows of characters, one character per pixel, against a palette — the
// way pixel art is actually authored. That is the whole point of doing it this way rather than
// stacking rectangles: a chair can have a back, a seat, legs and a shadow, with its own
// silhouette and its own light side, instead of being a box with a smaller box on it.
//
// Rules the drawing keeps:
// - every pixel is a whole pixel of the internal canvas (``fillRect`` on integers);
// - no gradients, no blur, no smoothing — shading is discrete tones from the ramps below;
// - ``.`` is transparent, so shapes are shapes and not bounding boxes.
//
// Sprites are drawn through ``drawSprite``, which batches a row of identical pixels into one
// fill: a 16×16 sprite costs a handful of fills, not 256.

export type Palette = Record<string, string>;

export interface Sprite {
  w: number;
  h: number;
  rows: string[];
  palette: Palette;
  /** How far down the sprite its "feet" are, for sorting by depth (defaults to its height). */
  anchor?: number;
}

export function sprite(rows: string[], palette: Palette, anchor?: number): Sprite {
  const w = Math.max(...rows.map((row) => row.length));
  return { w, h: rows.length, rows: rows.map((row) => row.padEnd(w, ".")), palette, anchor };
}

export interface DrawOptions {
  /** Characters to repaint in another colour: ``{ S: "#e0533d" }`` for a role's shirt. */
  recolor?: Palette;
  /** Mirror horizontally — one sprite, two directions. */
  flip?: boolean;
  alpha?: number;
}

export function drawSprite(
  ctx: CanvasRenderingContext2D,
  art: Sprite,
  x: number,
  y: number,
  options: DrawOptions = {},
): void {
  const { recolor, flip, alpha } = options;
  if (alpha !== undefined) ctx.globalAlpha = alpha;
  for (let row = 0; row < art.h; row++) {
    const line = art.rows[row];
    let runStart = 0;
    let runKey = "";
    const flush = (end: number) => {
      if (!runKey || runKey === ".") return;
      const colour = recolor?.[runKey] ?? art.palette[runKey];
      if (!colour) return;
      ctx.fillStyle = colour;
      const width = end - runStart;
      const left = flip ? art.w - end : runStart;
      ctx.fillRect(x + left, y + row, width, 1);
    };
    for (let col = 0; col < art.w; col++) {
      const key = line[col] ?? ".";
      if (key !== runKey) {
        flush(col);
        runStart = col;
        runKey = key;
      }
    }
    flush(art.w);
  }
  if (alpha !== undefined) ctx.globalAlpha = 1;
}

/** Where a sprite's feet are: what a top-down scene sorts by so nearer things cover farther ones. */
export function footOf(art: Sprite): number {
  return art.anchor ?? art.h;
}

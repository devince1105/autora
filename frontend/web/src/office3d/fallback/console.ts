// The 2D office's own look (T-410 stage 2): a dark console, drawn rather than fetched.
//
// The reference is a pixel-art floor in a trading terminal. Everything here is geometry and
// colour — no sprite sheet, no webfont — for three reasons: this view is also the fallback for
// a browser without WebGL and for a phone, so it must stay light; a Chinese pixel font is
// megabytes for the few dozen glyphs a label needs; and D-008 only allows CC0 assets, which is
// a question that does not arise if nothing is downloaded.
//
// The pixel feel comes from what pixel art is made of: a small palette, hard edges, whole-pixel
// steps, and shapes built out of rectangles. `image-rendering: pixelated` on the floor keeps the
// steps crisp when it is scaled up.
//
// The five 3D themes (D-011) are not repeated here. The office in 3D is a room you decorate;
// the 2D office is an instrument panel, and an instrument panel has one look.

export const CONSOLE = {
  /** Behind everything: the terminal's own dark. */
  bg: "#10201c",
  /** Panels, cards, the floor's slab. */
  panel: "#16302a",
  panelDim: "#12271f",
  /** Lines and frames; ``edge`` is the lit one that gives a box its pixel border. */
  edge: "#2f5f4f",
  edgeDim: "#1d3f35",
  text: "#d8efe2",
  textDim: "#7ba894",
  /** The floor of the room and its walkway. */
  floor: "#1b3a31",
  floorAlt: "#15302a",
  walkway: "#2c6050",
  /** Furniture. */
  desk: "#3c6a56",
  deskTop: "#4a7f66",
  screen: "#7fe3c0",
  chair: "#24473c",
  plant: "#3f8f5f",
  /** State: the same meanings the badges carry. */
  ok: "#68d6a4",
  warn: "#e8c15c",
  danger: "#e2725b",
  accent: "#7fe3c0",
} as const;

/** The agent badges' tones (``visual.badge.tone``), which are not the event tones below. */
export const BADGE_INK: Record<string, string> = {
  muted: CONSOLE.textDim,
  info: "#8fb7ff",
  active: CONSOLE.accent,
  warn: CONSOLE.warn,
  error: CONSOLE.danger,
  success: CONSOLE.ok,
};

/** An event's tone in the log (``events/describe``). */
export const TONE_INK: Record<string, string> = {
  neutral: CONSOLE.textDim,
  think: "#8fb7ff",
  work: CONSOLE.accent,
  review: "#c9a0ff",
  ok: CONSOLE.ok,
  warn: CONSOLE.warn,
  danger: CONSOLE.danger,
};

/** A box in the console's style: hard edges, two-tone border, no rounding. */
export const BOX = "border-2 border-[color:var(--console-edge-dim)] bg-[color:var(--console-panel)]";

/**
 * The CSS variables the 2D office paints itself with, set on its root element so nothing
 * outside it is touched — the admin pages around it keep the app's own theme.
 */
export function consoleVars(): Record<string, string> {
  return {
    "--console-bg": CONSOLE.bg,
    "--console-panel": CONSOLE.panel,
    "--console-panel-dim": CONSOLE.panelDim,
    "--console-edge": CONSOLE.edge,
    "--console-edge-dim": CONSOLE.edgeDim,
    "--console-text": CONSOLE.text,
    "--console-text-dim": CONSOLE.textDim,
    "--console-accent": CONSOLE.accent,
  };
}

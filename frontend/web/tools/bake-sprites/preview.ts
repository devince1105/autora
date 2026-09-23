// A contact sheet for choosing how the bake looks (D-026): the same furniture at a few camera
// angles and resolutions, and at the chosen one in every office style. Not part of the site —
// ``bake.mjs --preview`` renders it and saves a picture, so the choice is made by looking.
import { THEMES, ROLE_COLOR, type ThemeId } from "@/office3d/palette";
import { drawSprite } from "@/office3d/fallback/art/sprites";
import { paint, paintShadow, SHADOW_ALPHA } from "@/office3d/fallback/art/theme";

import { BAKEABLE } from "@/office3d/scene/furniture";

import { bakeOne, type BakedSprite } from "./render";

// representative pieces, found by what they are rather than by where they stand in the list
const PIECES = ["desk", "chair", ":plant", ":lounge", ":low_shelf", ":cafe_table", ":palm"].map(
  (want) => Object.keys(BAKEABLE).find((key) => (want.startsWith(":") ? key.endsWith(want) : key === want))!,
);
const ZOOM = 3;

function asPiece(s: BakedSprite) {
  const keys = Object.fromEntries(Object.entries(s.keys).map(([k, v]) => [k, [v.slot, v.tone] as const]));
  return { w: s.w, h: s.h, anchor: s.anchor, origin: [s.originX, s.originY] as const, footprint: s.footprint, keys, rows: s.rows, shadow: s.shadow };
}

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number) {
  ctx.fillStyle = "#e8ece6";
  ctx.font = "13px ui-monospace, Menlo, monospace";
  ctx.fillText(text, x, y);
}

interface Column {
  title: string;
  elevation: number;
  ppm: number;
  theme: ThemeId;
}

function sheet(title: string, columns: Column[]): HTMLCanvasElement {
  const baked = columns.map((c) => PIECES.map((name) => asPiece(bakeOne(name, c.elevation, c.ppm))));
  const colWidth = columns.map((_, i) => Math.max(...baked[i].map((p) => p.w)) * ZOOM + 24);
  const rowHeight = PIECES.map((_, r) => Math.max(...baked.map((col) => col[r].h)) * ZOOM + 16);
  const width = 110 + colWidth.reduce((a, b) => a + b, 0);
  const height = 60 + rowHeight.reduce((a, b) => a + b, 0);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = "#1b1f24";
  ctx.fillRect(0, 0, width, height);
  label(ctx, title, 12, 20);

  let x = 110;
  columns.forEach((c, i) => {
    const palette = THEMES[c.theme].palette;
    ctx.fillStyle = palette.floors.base.color;
    ctx.fillRect(x - 6, 36, colWidth[i] - 12, height - 42);
    label(ctx, c.title, x, 30);
    let y = 44;
    PIECES.forEach((name, r) => {
      if (i === 0) label(ctx, name, 12, y + 16);
      const art = paint(baked[i][r], palette, ROLE_COLOR.writer);
      // draw at 1:1 into a scratch canvas, then scale up without smoothing: chunky by construction
      const scratch = document.createElement("canvas");
      scratch.width = art.w;
      scratch.height = art.h;
      const shadow = paintShadow(baked[i][r]);
      if (shadow) drawSprite(scratch.getContext("2d")!, shadow, 0, 0, { alpha: SHADOW_ALPHA });
      drawSprite(scratch.getContext("2d")!, art, 0, 0);
      ctx.drawImage(scratch, x, y, art.w * ZOOM, art.h * ZOOM);
      y += rowHeight[r];
    });
    x += colWidth[i];
  });
  return canvas;
}

declare global {
  interface Window {
    previewSheets: () => string[];
  }
}

window.previewSheets = () => {
  const angles = sheet("角度 × 解析度（日式無印）", [
    { title: "45° 32px/m", elevation: 45, ppm: 32, theme: "muji" as ThemeId },
    { title: "60° 32px/m", elevation: 60, ppm: 32, theme: "muji" as ThemeId },
    { title: "75° 32px/m", elevation: 75, ppm: 32, theme: "muji" as ThemeId },
    { title: "60° 24px/m", elevation: 60, ppm: 24, theme: "muji" as ThemeId },
    { title: "60° 16px/m（現在）", elevation: 60, ppm: 16, theme: "muji" as ThemeId },
  ]);
  const themes = sheet(
    "同一次烘焙，五種風格（60° 32px/m）",
    (Object.keys(THEMES) as ThemeId[]).map((key) => ({ title: THEMES[key].label, elevation: 60, ppm: 32, theme: key })),
  );
  return [angles.toDataURL("image/png"), themes.toDataURL("image/png")];
};

// --- the whole floor, through the page's own painter ---------------------------------------------

import { floorPlan, type BoardCard } from "@/office3d/fallback/board";
import { paintFloor, Pictures } from "@/office3d/fallback/floorPainter";
import { buildScene } from "@/office3d/fallback/tiles";
import { assignSeats } from "@/office3d/scene/layout";

/** A company to fill the seats: one of each role, so every chair colour shows. */
const STAFF = [
  { id: "ceo-1", role: "ceo" },
  { id: "researcher-1", role: "researcher" },
  { id: "analyst-1", role: "analyst" },
  { id: "writer-1", role: "writer" },
  { id: "editor-1", role: "editor" },
  { id: "marketing-1", role: "marketing" },
];

function floor(theme: ThemeId, focused: string | null = null, zoom = 2): HTMLCanvasElement {
  const plan = floorPlan(
    STAFF.map((s) => s.id),
    assignSeats(STAFF).seats,
  );
  const cards = new Map(STAFF.map((s) => [s.id, { id: s.id, color: ROLE_COLOR[s.role] } as unknown as BoardCard]));
  const scene = buildScene(plan, cards);
  const frame = document.createElement("canvas");
  frame.width = scene.width;
  frame.height = scene.height;
  paintFloor(frame.getContext("2d")!, scene, new Pictures(theme), {
    selected: focused ? "editor-1" : null,
    focused,
    walking: [],
    colours: new Map(),
    time: 0,
  });
  const big = document.createElement("canvas");
  big.width = scene.width * zoom;
  big.height = scene.height * zoom;
  const ctx = big.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(frame, 0, 0, big.width, big.height);
  return big;
}

declare global {
  interface Window {
    previewFloors: () => Record<string, string>;
  }
}

window.previewFloors = () => {
  const out: Record<string, string> = {};
  for (const id of Object.keys(THEMES) as ThemeId[]) out[`floor-${id}.png`] = floor(id).toDataURL("image/png");
  out["floor-muji-editorial-chosen.png"] = floor("muji", "editorial").toDataURL("image/png");
  return out;
};

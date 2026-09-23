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

/** People out of their chairs, so one picture shows every way a figure is drawn. */
const WALKERS = [
  { agentId: "walker-right", position: [-1.5, -2.2] as const, heading: Math.PI / 2, carrying: true },
  { agentId: "walker-left", position: [3.5, 2.4] as const, heading: -Math.PI / 2 },
  { agentId: "walker-toward", position: [0, 0.2] as const, heading: 0 },
  { agentId: "walker-away", position: [7.2, -2.6] as const, heading: Math.PI },
];

function floor(theme: ThemeId, focused: string | null = null, zoom = 2): HTMLCanvasElement {
  const plan = floorPlan(
    STAFF.map((s) => s.id),
    assignSeats(STAFF).seats,
  );
  // the marketer is done and has stood up behind the chair
  const cards = new Map(
    STAFF.map((s) => [
      s.id,
      { id: s.id, color: ROLE_COLOR[s.role], visual: { pose: s.role === "marketing" ? "stand" : "sit_idle" } } as unknown as BoardCard,
    ]),
  );
  const characters = new Map<string, string>([
    ...STAFF.map((s) => [s.id, characterFor(s.id)] as [string, string]),
    ["walker-right", "character-female-c"],
    ["walker-left", "character-male-e"],
    ["walker-toward", "character-female-b"],
    ["walker-away", "character-male-c"],
  ]);
  const scene = buildScene(plan, cards, characters);
  const frame = document.createElement("canvas");
  frame.width = scene.width;
  frame.height = scene.height;
  paintFloor(frame.getContext("2d")!, scene, new Pictures(theme), {
    selected: focused ? "editor-1" : null,
    focused,
    walking: WALKERS,
    characters,
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

// --- the people ------------------------------------------------------------------------------------

import { CHARACTERS, characterFor } from "@/office3d/assets/characters";

import { bakePeople, personKey, WALK_FRAMES } from "./people";

declare global {
  interface Window {
    previewPeople: () => Promise<string>;
  }
}

/** Every character in every pose, at four times size: the sheet the people are judged by. */
window.previewPeople = async () => {
  const { sprites } = await bakePeople();
  const byName = new Map(sprites.map((s) => [s.name, asPiece(s)]));
  const columns: [string, string, number][] = [["sit", "away", 0]];
  for (const dir of ["toward", "away", "side"]) {
    for (let f = 0; f < WALK_FRAMES; f++) columns.push(["walk", dir, f]);
    columns.push(["stand", dir, 0]);
  }
  const zoom = 4;
  const cell = 48 * zoom;
  const canvas = document.createElement("canvas");
  canvas.width = 160 + columns.length * cell;
  canvas.height = 40 + CHARACTERS.length * cell;
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  ctx.fillStyle = THEMES.muji.palette.floors.base.color;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  columns.forEach(([pose, dir, f], c) => {
    ctx.fillStyle = "#333";
    ctx.font = "12px ui-monospace, Menlo, monospace";
    ctx.fillText(`${pose} ${dir}${pose === "walk" ? ` ${f}` : ""}`, 160 + c * cell + 4, 24);
  });
  CHARACTERS.forEach((character, r) => {
    ctx.fillStyle = "#333";
    ctx.fillText(character.replace("character-", ""), 8, 40 + r * cell + cell / 2);
    columns.forEach(([pose, dir, f], c) => {
      const piece = byName.get(personKey(character, pose, dir, f));
      if (!piece) return;
      const art = paint(piece, THEMES.muji.palette);
      const shadow = paintShadow(piece);
      const scratch = document.createElement("canvas");
      scratch.width = piece.w;
      scratch.height = piece.h;
      const sctx = scratch.getContext("2d")!;
      if (shadow) drawSprite(sctx, shadow, 0, 0, { alpha: SHADOW_ALPHA });
      drawSprite(sctx, art, 0, 0);
      ctx.drawImage(scratch, 160 + c * cell + (cell - piece.w * zoom) / 2, 40 + r * cell + (cell - piece.h * zoom) / 2, piece.w * zoom, piece.h * zoom);
    });
  });
  return canvas.toDataURL("image/png");
};

// --- how long the backdrop takes to paint, for measuring the 2D board's first frame -------------

import { BACKDROP_KEY } from "@/office3d/fallback/art/pieces";

declare global {
  interface Window {
    timeBackdrop: (theme: ThemeId) => number;
  }
}

/** Milliseconds to paint the backdrop in a style from nothing: what the 2D board's first frame pays. */
window.timeBackdrop = (theme) => {
  const pictures = new Pictures(theme);
  const t0 = performance.now();
  pictures.art(BACKDROP_KEY);
  return performance.now() - t0;
};

declare global {
  interface Window {
    timeFirstFrame: (theme: ThemeId) => number;
  }
}

/** Milliseconds for the 2D board's first frame from an empty cache: every picture painted once. */
window.timeFirstFrame = (theme) => {
  const plan = floorPlan(STAFF.map((s) => s.id), assignSeats(STAFF).seats);
  const cards = new Map(STAFF.map((s) => [s.id, { id: s.id, color: ROLE_COLOR[s.role], visual: { pose: "sit_idle" } } as unknown as BoardCard]));
  const characters = new Map<string, string>(STAFF.map((s) => [s.id, characterFor(s.id)]));
  const frame = document.createElement("canvas");
  const t0 = performance.now();
  const scene = buildScene(plan, cards, characters);
  frame.width = scene.width;
  frame.height = scene.height;
  paintFloor(frame.getContext("2d")!, scene, new Pictures(theme), { selected: null, focused: null, walking: [], characters, time: 0 });
  return performance.now() - t0;
};

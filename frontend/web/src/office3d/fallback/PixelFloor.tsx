// The floor, drawn as a tile-based pixel-art scene on a canvas (T-410).
//
// The canvas has its own small resolution (one tile = 16 px = one metre of the real floor plan)
// and the browser scales it up with nearest-neighbour: every pixel on screen is a whole pixel
// of the drawing. Everything visible is a sprite from ``art/atlas`` — tiles, furniture, props
// and people alike — drawn through ``drawSprite``. Nothing here is a gradient, a blur or a
// smoothed vector, and no object is a rectangle standing in for a thing.
//
// Depth: the scene is painted back to front by each thing's feet, so a person in front of a
// desk covers it and one behind it does not. Walls get a top face and a front face, which is
// what gives a room its height from above.
//
// This file paints; it decides nothing. What exists and where is ``tiles.buildScene``, which is
// pure and tested without a canvas.
"use client";

import { useEffect, useMemo, useRef } from "react";

import { uiStore } from "@/stores/ui";

import { useRoster } from "../agents/roster";
import { CueDirector, routeFor } from "../visual/CueRunner";
import * as atlas from "./art/atlas";
import { drawSprite } from "./art/sprites";
import type { BoardCard, FloorPlan } from "./board";
import { CONSOLE } from "./console";
import { at, buildScene, foot, hitTest, metresToPixels, NPC, TILE, zoneAt, type Scene } from "./tiles";
import { walkersNow, type Walker } from "./walkers";

const DIM = 0.45;

declare global {
  interface Window {
    /** Who is walking across the 2D floor right now, for browser tests; not an API. The 3D
     * office publishes the same thing under ``__autoraOfficeCues``. */
    __autoraOfficeFloor?: { walking: string[]; walks: number };
  }
}

let lastWalking = "";

function probe(walking: Walker[]): void {
  if (typeof window === "undefined") return;
  const ids = walking.map((walker) => walker.agentId);
  const key = ids.join(",");
  if (key === lastWalking) return;
  const started = ids.filter((id) => !lastWalking.includes(id)).length;
  window.__autoraOfficeFloor = { walking: ids, walks: (window.__autoraOfficeFloor?.walks ?? 0) + started };
  lastWalking = key;
}

/** The ground: a tile sprite per square, and walls with a top and a face. */
function paintGround(ctx: CanvasRenderingContext2D, scene: Scene, focused: string | null) {
  for (let row = 0; row < scene.map.rows; row++) {
    for (let col = 0; col < scene.map.cols; col++) {
      const kind = at(scene.map, col, row);
      const zone = zoneAt(scene.map, col, row);
      const alpha = focused && zone && zone !== focused ? DIM : 1;
      const x = col * TILE;
      const y = row * TILE;
      if (kind === "outside") continue;
      if (kind === "wall") {
        drawSprite(ctx, atlas.WALL_TOP, x, y, { alpha });
        // the face only where the wall meets open floor: that is where height shows
        const below = at(scene.map, col, row + 1);
        if (below !== "wall" && below !== "outside") drawSprite(ctx, atlas.WALL_FACE, x, y + TILE, { alpha });
        continue;
      }
      if (kind === "corridor") {
        drawSprite(ctx, atlas.WALKWAY, x, y, { alpha });
        continue;
      }
      if (kind === "carpet" || kind === "room_floor") {
        drawSprite(ctx, atlas.CARPET, x, y, {
          alpha,
          // the rooms at the back have a wooden floor, lighter than the open plan's carpet
          recolor: kind === "room_floor" ? { C: "#6b4e34", D: "#7d5c3e", E: "#8a6743" } : undefined,
        });
        continue;
      }
      drawSprite(ctx, atlas.FLOOR[(col * 3 + row * 7) % atlas.FLOOR.length], x, y, { alpha });
    }
  }
}

/** A person in their role's colours, with a frame for whoever is selected. */
function paintPerson(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  options: { step?: 0 | 1; carrying?: boolean; selected?: boolean; alpha?: number } = {},
) {
  const { step = 0, carrying, selected, alpha } = options;
  if (selected) {
    ctx.globalAlpha = alpha ?? 1;
    ctx.fillStyle = CONSOLE.accent;
    ctx.fillRect(x - 1, y - 1, NPC.w + 2, 1);
    ctx.fillRect(x - 1, y + NPC.h, NPC.w + 2, 1);
    ctx.fillRect(x - 1, y, 1, NPC.h);
    ctx.fillRect(x + NPC.w, y, 1, NPC.h);
    ctx.globalAlpha = 1;
  }
  drawSprite(ctx, atlas.PERSON[step], x, y, { alpha, recolor: { S: color, H: shadeOf(color) } });
  if (carrying) drawSprite(ctx, atlas.DOCUMENT, x + NPC.w - 2, y + 8, { alpha });
}

/** A darker step of a role's colour, for hair against the shirt. */
function shadeOf(color: string): string {
  const value = Number.parseInt(color.replace("#", ""), 16);
  if (Number.isNaN(value)) return "#2a1d16";
  const dark = [(value >> 16) & 255, (value >> 8) & 255, value & 255].map((c) => Math.round(c * 0.45));
  return `#${dark.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

export function PixelFloor({
  plan,
  cards,
  selected,
  focused,
}: {
  plan: FloorPlan;
  /** The people on the floor, by id. */
  cards: Map<string, BoardCard>;
  selected: string | null;
  /** The room the tabs are on, or null for the whole floor. */
  focused: string | null;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<Scene | null>(null);
  scene.current = buildScene(plan, cards);
  const roster = useRoster();
  const latest = useRef({ roster, selected, focused, plan, cards });
  latest.current = { roster, selected, focused, plan, cards };

  // The 3D office walks its couriers from a frame hook inside its canvas. This one has no such
  // hook, so it runs the same queue itself: one director per board, stepped every frame.
  const director = useMemo(() => new CueDirector(), []);
  useEffect(() => () => director.dispose(), [director]);

  useEffect(() => {
    const element = canvas.current;
    const ctx = element?.getContext?.("2d");
    if (!element || !ctx) return; // no canvas in this environment: the tests' case
    let frame = 0;
    const draw = (time: number) => {
      const current = scene.current;
      const { roster: members, selected: chosen, focused: room, plan: floor, cards: people } = latest.current;
      if (!current) return;
      director.queue.step(time, (cue) => routeFor(cue, members)?.durationMs ?? null);
      const walking = walkersNow(director, members, time);
      const moving = new Set(walking.map((walker) => walker.agentId));

      element.width = current.width;
      element.height = current.height;
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = CONSOLE.bg;
      ctx.fillRect(0, 0, current.width, current.height);
      paintGround(ctx, current, room);

      // everything that stands on the floor, painted back to front by its feet
      const standing: { foot: number; paint: () => void }[] = [];
      for (const prop of current.props) {
        const alpha = room && prop.zone && prop.zone !== room ? DIM : 1;
        standing.push({
          foot: prop.kind === "rug" ? -1 : foot(prop), // a rug is ground, and goes under everything
          paint: () => drawSprite(ctx, prop.art, prop.x, prop.y, { alpha, flip: prop.flip }),
        });
      }
      for (const npc of current.npcs) {
        if (moving.has(npc.agentId)) continue; // out of their chair
        const alpha = room && npc.zone && npc.zone !== room ? DIM : 1;
        standing.push({
          foot: npc.y + NPC.h,
          paint: () => paintPerson(ctx, npc.x, npc.y, npc.color, { selected: npc.agentId === chosen, alpha }),
        });
      }
      for (const walker of walking) {
        const card = people.get(walker.agentId);
        if (!card) continue;
        const spot = metresToPixels(walker.position[0], walker.position[1], floor);
        const x = Math.round(spot.x - NPC.w / 2);
        const y = Math.round(spot.y - NPC.h + 6);
        standing.push({
          foot: y + NPC.h,
          paint: () =>
            paintPerson(ctx, x, y, card.color, {
              step: Math.floor(time / 170) % 2 === 0 ? 0 : 1,
              carrying: walker.carrying,
              selected: walker.agentId === chosen,
            }),
        });
      }
      standing.sort((left, right) => left.foot - right.foot);
      for (const item of standing) item.paint();
      ctx.globalAlpha = 1;
      probe(walking);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [director]);

  const room = focused ? (plan.rooms.find((r) => r.id === focused)?.label ?? focused) : null;
  return (
    <canvas
      ref={canvas}
      data-testid="room-plan"
      role="img"
      aria-label={room ? `樓層（俯視，${room}）` : "樓層（俯視）"}
      width={scene.current.width}
      height={scene.current.height}
      className="h-full w-full border-2 border-[color:var(--console-edge-dim)] object-contain"
      style={{ imageRendering: "pixelated", background: CONSOLE.bg }}
      onClick={(event) => {
        const current = scene.current;
        const element = canvas.current;
        if (!current || !element) return;
        const box = element.getBoundingClientRect();
        // the canvas is letterboxed by object-contain: undo that before hit-testing
        const scale = Math.min(box.width / current.width, box.height / current.height);
        const x = (event.clientX - box.left - (box.width - current.width * scale) / 2) / scale;
        const y = (event.clientY - box.top - (box.height - current.height * scale) / 2) / scale;
        const hit = hitTest(current, x, y);
        if (hit) uiStore.getState().selectAgent(hit);
      }}
    />
  );
}

// The floor, drawn as a tile-based pixel-art scene on a canvas (T-410 stage 3).
//
// The canvas is its own small resolution (one tile = 16 px = one metre of the real floor plan)
// and is scaled up by the browser with nearest-neighbour: every pixel on screen is a whole
// pixel of the drawing, stair-stepped edges and all. Nothing here is a gradient, a shadow or a
// smoothed vector — the whole scene is ``fillRect`` on integer coordinates, which is what makes
// it pixel art rather than a picture with a pixel filter over it.
//
// What is drawn comes from ``tiles.buildScene``, which is pure and tested; this file only paints.
"use client";

import { useEffect, useMemo, useRef } from "react";

import { uiStore } from "@/stores/ui";

import { useRoster } from "../agents/roster";
import { CueDirector, routeFor } from "../visual/CueRunner";
import type { BoardCard, FloorPlan } from "./board";
import { CONSOLE } from "./console";
import { at, buildScene, hitTest, metresToPixels, NPC, TILE, zoneAt, type Prop, type Scene, type TileKind } from "./tiles";
import { walkersNow, type Walker } from "./walkers";

const DIM = 0.4;

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

const TILE_INK: Record<TileKind, string | null> = {
  outside: CONSOLE.bg,
  wall: "#0c1a16",
  floor: CONSOLE.floor,
  floor_alt: CONSOLE.floorAlt,
  corridor: CONSOLE.walkway,
  carpet: "#1f473c",
  room_floor: "#1a352d",
};

const WALL_TOP = "#27584a";

function paintTiles(ctx: CanvasRenderingContext2D, scene: Scene, focused: string | null) {
  for (let row = 0; row < scene.map.rows; row++) {
    for (let col = 0; col < scene.map.cols; col++) {
      const kind = at(scene.map, col, row);
      const zone = zoneAt(scene.map, col, row);
      ctx.globalAlpha = focused && zone && zone !== focused ? DIM : 1;
      ctx.fillStyle = TILE_INK[kind] ?? CONSOLE.floor;
      ctx.fillRect(col * TILE, row * TILE, TILE, TILE);
      if (kind === "wall") {
        // the lit edge only where the wall actually turns into floor: drawing it on every wall
        // tile made the side walls read as a dashed line rather than a wall
        if (at(scene.map, col, row - 1) !== "wall") {
          ctx.fillStyle = WALL_TOP;
          ctx.fillRect(col * TILE, row * TILE, TILE, 3);
        }
        if (at(scene.map, col - 1, row) !== "wall") {
          ctx.fillStyle = "#1b3f35";
          ctx.fillRect(col * TILE, row * TILE, 2, TILE);
        }
      }
      if (kind === "carpet" && (col + row) % 3 === 0) {
        ctx.fillStyle = "#235244";
        ctx.fillRect(col * TILE + 4, row * TILE + 4, 3, 3);
      }
      if (kind === "floor" || kind === "floor_alt") {
        ctx.fillStyle = "#14302a";
        ctx.fillRect(col * TILE, row * TILE, TILE, 1);
        ctx.fillRect(col * TILE, row * TILE, 1, TILE);
      }
    }
  }
  ctx.globalAlpha = 1;
}

function paintProp(ctx: CanvasRenderingContext2D, prop: Prop) {
  const { x, y, w, h } = prop;
  switch (prop.kind) {
    case "desk": {
      ctx.fillStyle = CONSOLE.desk;
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = CONSOLE.deskTop;
      ctx.fillRect(x, prop.facing === "down" ? y + h - 3 : y, w, 3);
      // two monitors, their backs to whoever sits down
      ctx.fillStyle = "#0f2a24";
      ctx.fillRect(x + 3, y + 3, 7, 5);
      ctx.fillRect(x + w - 10, y + 3, 7, 5);
      ctx.fillStyle = CONSOLE.screen;
      ctx.fillRect(x + 4, y + 4, 5, 3);
      ctx.fillRect(x + w - 9, y + 4, 5, 3);
      break;
    }
    case "chair": {
      ctx.fillStyle = CONSOLE.chair;
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#1a352d";
      ctx.fillRect(x + 2, y + 2, w - 4, h - 4);
      break;
    }
    case "counter": {
      ctx.fillStyle = CONSOLE.deskTop;
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#2b6152";
      ctx.fillRect(x, y + h - 3, w, 3);
      break;
    }
    case "sofa": {
      ctx.fillStyle = "#2f5f7a";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#3d7796";
      ctx.fillRect(x + 2, y + 2, w - 4, h - 6);
      ctx.fillStyle = "#24485c";
      ctx.fillRect(x, y + h - 4, w, 4);
      break;
    }
    case "shelf": {
      ctx.fillStyle = "#4a3b2a";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#6b5738";
      for (let i = 0; i < w; i += 6) ctx.fillRect(x + i + 1, y + 1, 4, h - 2);
      break;
    }
    case "fridge": {
      ctx.fillStyle = "#cfe8dd";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#9fc4b6";
      ctx.fillRect(x, y + Math.round(h / 2), w, 2);
      ctx.fillRect(x + w - 4, y + 4, 2, 5);
      break;
    }
    case "stove": {
      ctx.fillStyle = "#26423a";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#e8c15c";
      for (const [dx, dy] of [
        [4, 4],
        [w - 8, 4],
        [4, h - 7],
        [w - 8, h - 7],
      ]) {
        ctx.fillRect(x + dx, y + dy, 3, 3);
      }
      break;
    }
    case "whiteboard": {
      ctx.fillStyle = "#dceee6";
      ctx.fillRect(x, y, w, h);
      ctx.fillStyle = "#8fb7ff";
      ctx.fillRect(x + 3, y + 2, Math.max(4, Math.round(w / 3)), 2);
      break;
    }
    case "plant": {
      ctx.fillStyle = "#6b4630";
      ctx.fillRect(x + 1, y + h - 5, w - 2, 5);
      ctx.fillStyle = CONSOLE.plant;
      ctx.fillRect(x, y + 2, w, h - 7);
      ctx.fillStyle = "#57b877";
      ctx.fillRect(x + 2, y, 3, 6);
      break;
    }
    case "door": {
      ctx.fillStyle = CONSOLE.accent;
      ctx.fillRect(x, y, Math.max(3, Math.round(w / 3)), h);
      break;
    }
  }
}

/** A person, 10×14 pixels: hair, face, body in the role's colour, two legs.
 *
 * ``step`` alternates the legs while they walk: two frames, like the sprite sheets this is
 * pretending to be. A walker carries a page when they are taking something somewhere. */
function paintNpc(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  selected: boolean,
  options: { step?: 0 | 1; carrying?: boolean } = {},
) {
  if (selected) {
    ctx.fillStyle = CONSOLE.accent;
    ctx.fillRect(x - 2, y - 2, NPC.w + 4, NPC.h + 4);
  }
  ctx.fillStyle = color;
  ctx.fillRect(x + 1, y, NPC.w - 2, 4); // hair
  ctx.fillStyle = "#f0d2b4";
  ctx.fillRect(x + 2, y + 3, NPC.w - 4, 4); // face
  ctx.fillStyle = "#1b3129";
  ctx.fillRect(x + 3, y + 5, 2, 1); // eyes
  ctx.fillRect(x + NPC.w - 5, y + 5, 2, 1);
  ctx.fillStyle = color;
  ctx.fillRect(x, y + 7, NPC.w, 5); // body
  ctx.fillStyle = "#132a24";
  const step = options.step ?? 0;
  ctx.fillRect(x + 1, y + 12, 3, step === 0 ? 2 : 1); // legs, mid-stride on the odd frame
  ctx.fillRect(x + NPC.w - 4, y + 12, 3, step === 0 ? 1 : 2);
  if (options.carrying) {
    ctx.fillStyle = "#e8e2cf";
    ctx.fillRect(x + NPC.w - 2, y + 7, 4, 5);
    ctx.fillStyle = "#9aa08c";
    ctx.fillRect(x + NPC.w - 2, y + 9, 4, 1);
  }
}

/** Somebody between desks: on the corridor where the route says, legs alternating. */
function paintWalker(
  ctx: CanvasRenderingContext2D,
  walker: Walker,
  scene: Scene,
  plan: FloorPlan,
  cards: Map<string, BoardCard>,
  selected: string | null,
  time: number,
) {
  const card = cards.get(walker.agentId);
  if (!card) return;
  const spot = metresToPixels(walker.position[0], walker.position[1], plan);
  const x = Math.round(spot.x - NPC.w / 2);
  const y = Math.round(spot.y - NPC.h + 4);
  if (x < 0 || y < 0 || x > scene.width || y > scene.height) return;
  paintNpc(ctx, x, y, card.color, walker.agentId === selected, {
    step: Math.floor(time / 180) % 2 === 0 ? 0 : 1,
    carrying: walker.carrying,
  });
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
  const latest = useRef({ roster, selected, focused, plan });
  latest.current = { roster, selected, focused, plan };

  // The 3D office walks its couriers with a frame hook inside its canvas. This one has no such
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
      const { roster: members, selected: chosen, focused: room, plan: floor } = latest.current;
      if (!current) return;
      director.queue.step(time, (cue) => routeFor(cue, members)?.durationMs ?? null);
      const walking = walkersNow(director, members, time);
      const moving = new Set(walking.map((walker) => walker.agentId));
      element.width = current.width;
      element.height = current.height;
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = CONSOLE.bg;
      ctx.fillRect(0, 0, current.width, current.height);
      paintTiles(ctx, current, room);
      for (const prop of current.props) {
        ctx.globalAlpha = room && prop.zone && prop.zone !== room ? DIM : 1;
        paintProp(ctx, prop);
      }
      for (const npc of current.npcs) {
        if (moving.has(npc.agentId)) continue; // they are out of their chair
        ctx.globalAlpha = room && npc.zone && npc.zone !== room ? DIM : 1;
        paintNpc(ctx, npc.x, npc.y, npc.color, npc.agentId === chosen);
      }
      ctx.globalAlpha = 1;
      for (const walker of walking) paintWalker(ctx, walker, current, floor, cards, chosen, time);
      probe(walking);
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [director, cards]);

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

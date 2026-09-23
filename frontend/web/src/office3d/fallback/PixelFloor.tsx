// The floor, drawn as pixel art on a canvas (T-410; D-026, D-027).
//
// Everything here was baked from the 3D office — the room's shell as one backdrop, each piece of
// furniture as its own sprite — and is painted in whichever of the office's styles is chosen, so
// switching 日式無印 to 電光風 in the settings repaints the 2D floor with it. The canvas has its
// own resolution (a metre of floor is 32 pixels across) and the browser scales it up with
// nearest-neighbour: every pixel on screen is a whole pixel of the drawing.
//
// This component only drives the frames: it steps the walking queue and hands the frame to
// ``floorPainter``, which is shared with the bake tool's preview. What exists and where is
// ``tiles.buildScene``, which is pure and tested without a canvas.
"use client";

import { useEffect, useMemo, useRef } from "react";

import { uiStore } from "@/stores/ui";

import { useRoster } from "../agents/roster";
import { DEFAULT_THEME, type ThemeId } from "../palette";
import { CueDirector, routeFor } from "../visual/CueRunner";
import type { BoardCard, FloorPlan } from "./board";
import { CONSOLE } from "./console";
import { paintFloor, Pictures } from "./floorPainter";
import { buildScene, hitTest, type Scene } from "./tiles";
import { walkersNow, type Walker } from "./walkers";

declare global {
  interface Window {
    /** Who is walking across the 2D floor right now, and in which style, for browser tests; not
     * an API. The 3D office publishes the same thing under ``__autoraOfficeCues``. */
    __autoraOfficeFloor?: { walking: string[]; walks: number; theme?: ThemeId };
  }
}

let lastWalking = "";

function probe(walking: Walker[], theme: ThemeId): void {
  if (typeof window === "undefined") return;
  const ids = walking.map((walker) => walker.agentId);
  const key = ids.join(",");
  const previous = window.__autoraOfficeFloor;
  if (key === lastWalking && previous?.theme === theme) return;
  const started = key === lastWalking ? 0 : ids.filter((id) => !lastWalking.includes(id)).length;
  window.__autoraOfficeFloor = { walking: ids, walks: (previous?.walks ?? 0) + started, theme };
  lastWalking = key;
}

export function PixelFloor({
  plan,
  cards,
  selected,
  focused,
  theme = DEFAULT_THEME,
}: {
  plan: FloorPlan;
  /** The people on the floor, by id. */
  cards: Map<string, BoardCard>;
  selected: string | null;
  /** The room the tabs are on, or null for the whole floor. */
  focused: string | null;
  /** The office's style (D-011): the same one the 3D view is painted in. */
  theme?: ThemeId;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<Scene | null>(null);
  scene.current = buildScene(plan, cards);
  const roster = useRoster();
  const pictures = useMemo(() => new Pictures(theme), [theme]);
  const latest = useRef({ roster, selected, focused, cards, pictures });
  latest.current = { roster, selected, focused, cards, pictures };

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
      const { roster: members, selected: chosen, focused: room, cards: people, pictures: art } = latest.current;
      if (!current) return;
      director.queue.step(time, (cue) => routeFor(cue, members)?.durationMs ?? null);
      const walking = walkersNow(director, members, time);

      if (element.width !== current.width) element.width = current.width;
      if (element.height !== current.height) element.height = current.height;
      paintFloor(ctx, current, art, {
        selected: chosen,
        focused: room,
        walking,
        colours: new Map([...people].map(([id, card]) => [id, card.color])),
        time,
      });
      probe(walking, art.theme);
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
      data-office-theme={theme}
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

// D-027: the people on the 2D floor are the 3D office's characters.
//
// The bake needs a browser and runs by hand (``pnpm -F web bake-sprites``); these check what the
// board relies on afterwards — every character in every pose, the right way round, and each one
// within its colour budget so it reads as pixel art rather than a shrunken render.
import { describe, expect, it } from "vitest";

import { CHARACTERS } from "@/office3d/assets/characters";
import { THEMES, type ThemeId } from "@/office3d/palette";

import { facingOf, figure, placeFigure, PEOPLE_KEYS, WALK_FRAMES } from "./people";
import { OUTLINE_TONE, paint } from "./theme";

/** How many colours a character may use (``COLOURS_PER_CHARACTER`` in the bake). */
const BUDGET = 10;

describe("the baked people", () => {
  it("has every character in every pose the board draws", () => {
    for (const character of CHARACTERS) {
      expect(figure(character, "sit", "away"), `${character} seated`).not.toBeNull();
      for (const facing of ["toward", "away", "right", "left"] as const) {
        expect(figure(character, "stand", facing), `${character} standing ${facing}`).not.toBeNull();
        for (let frame = 0; frame < WALK_FRAMES; frame++) {
          expect(figure(character, "walk", facing, frame), `${character} walking ${facing} ${frame}`).not.toBeNull();
        }
      }
    }
    // twelve characters: one seated, and walking (four frames) and standing three ways each
    expect(PEOPLE_KEYS).toHaveLength(CHARACTERS.length * (1 + 3 * (WALK_FRAMES + 1)));
  });

  it("keeps each character to a pixel artist's budget of colours, the same in every pose", () => {
    for (const character of CHARACTERS) {
      const colours = new Set<string>();
      for (const key of PEOPLE_KEYS.filter((k) => k.startsWith(`${character}|`))) {
        const [, pose, dir, frame] = key.split("|");
        const f = figure(character, pose as "sit", dir === "side" ? "right" : (dir as "away"), Number(frame));
        for (const [slot] of Object.values(f!.piece.keys)) colours.add(slot);
      }
      expect(colours.size, character).toBeLessThanOrEqual(BUDGET);
      for (const colour of colours) expect(colour, character).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("has an outline round every figure", () => {
    const f = figure(CHARACTERS[0], "walk", "toward", 0)!;
    expect(Object.values(f.piece.keys).some(([, tone]) => tone === OUTLINE_TONE)).toBe(true);
  });
});

describe("which way a figure faces", () => {
  it("follows the 3D office's heading: 0 toward the viewer, a quarter turn to the right", () => {
    expect(facingOf(0)).toBe("toward");
    expect(facingOf(Math.PI)).toBe("away");
    expect(facingOf(-Math.PI)).toBe("away");
    expect(facingOf(Math.PI / 2)).toBe("right");
    expect(facingOf(-Math.PI / 2)).toBe("left");
    expect(facingOf(2 * Math.PI + 0.1)).toBe("toward"); // however many turns
  });

  it("draws the left profile as the right one mirrored", () => {
    const right = figure(CHARACTERS[0], "walk", "right", 1)!;
    const left = figure(CHARACTERS[0], "walk", "left", 1)!;
    expect(left.piece).toBe(right.piece);
    expect([right.flip, left.flip]).toEqual([false, true]);
  });

  it("keeps the feet on the spot when mirrored", () => {
    const left = figure(CHARACTERS[0], "walk", "left", 0)!;
    const right = figure(CHARACTERS[0], "walk", "right", 0)!;
    const spot = { x: 100, y: 80 };
    const [l, r] = [placeFigure(left, spot), placeFigure(right, spot)];
    expect(l.y).toBe(r.y);
    expect(l.x + (left.piece.w - left.piece.origin[0])).toBe(spot.x);
    expect(r.x + right.piece.origin[0]).toBe(spot.x);
  });

  it("seats everybody facing their screens, whatever they were asked to face", () => {
    for (const facing of ["toward", "right", "left"] as const) {
      const f = figure(CHARACTERS[0], "sit", facing)!;
      expect(f.key).toContain("|sit|away|");
      expect(f.flip).toBe(false);
    }
  });

  it("walks through its frames and round again", () => {
    const frames = Array.from({ length: WALK_FRAMES * 2 }, (_, i) => figure(CHARACTERS[0], "walk", "toward", i)!.key);
    expect(new Set(frames).size).toBe(WALK_FRAMES);
    expect(frames[0]).toBe(frames[WALK_FRAMES]);
  });
});

describe("the people in the office's styles", () => {
  it("wear the same clothes in every style, as they do in 3D", () => {
    const f = figure(CHARACTERS[3], "stand", "toward")!;
    const inside = (id: ThemeId) =>
      Object.fromEntries(
        Object.entries(paint(f.piece, THEMES[id].palette).palette).filter(([key]) => f.piece.keys[key][1] !== OUTLINE_TONE),
      );
    for (const id of Object.keys(THEMES) as ThemeId[]) expect(inside(id), id).toEqual(inside("muji"));
  });

  it("are rimmed in neon in the style that glows, like everything else there", () => {
    const f = figure(CHARACTERS[3], "stand", "toward")!;
    const outline = Object.entries(f.piece.keys).find(([, [, tone]]) => tone === OUTLINE_TONE)![0];
    expect(paint(f.piece, THEMES.cyber.palette).palette[outline]).not.toBe(paint(f.piece, THEMES.muji.palette).palette[outline]);
  });
});

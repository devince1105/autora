// D-027: the floors' textures on the 2D board — the 3D office's, at the 3D office's spacing.
import { describe, expect, it } from "vitest";

import { THEMES, type FloorLook, type ThemeId } from "@/office3d/palette";

import { applyTouch, floorKindOf, patternFloors, touch, type FloorPixel } from "./floorPattern";

const ACROSS = 32;
const DEEP = 28;

/** The pixel at (col, row) of a floor whose world origin is pixel (0, 0). */
function at(col: number, row: number): FloorPixel {
  const w = (c: number, r: number) => ({ x: c / ACROSS, z: r / DEEP });
  return { col, row, here: w(col, row), left: w(col - 1, row), up: w(col, row - 1) };
}

/** The rows, down one column, where a floor draws a line across. */
function linesDown(look: FloorLook, col: number, rows: number): number[] {
  const out: number[] = [];
  for (let row = 1; row < rows; row++) {
    const t = touch(look, at(col, row));
    // a line across is the same on the next column over too; a plank or speckle is not
    if (t && JSON.stringify(t) === JSON.stringify(touch(look, at(col + 7, row))) && ("colour" in t || ("dark" in t && t.dark >= 0.1) || ("light" in t && t.light >= 0.3))) {
      out.push(row);
    }
  }
  return out;
}

describe("the floors' patterns", () => {
  it("lays planks in four rows to a texture tile, as the 3D texture does", () => {
    const wood: FloorLook = { color: "#e3cfad", roughness: 0.6, texture: "wood", metres: 1.2 };
    // 1.2 m of floor is 33.6 pixels deep: four plank rows, so a joint every 8.4 pixels. Two tiles
    // down is 67.2 rows and the eighth joint falls on row 68, so look a couple of rows further
    const rows = linesDown(wood, 3, Math.ceil(1.2 * DEEP * 2) + 2);
    expect(rows.length).toBe(8);
    expect(rows.every((r, i) => i === 0 || Math.abs(r - rows[i - 1] - 8.4) <= 1)).toBe(true);
  });

  it("puts the 3D office's grout between tiles, two to a texture tile", () => {
    const tile: FloorLook = { color: "#f4f2ed", roughness: 0.3, texture: "tile", metres: 0.5 };
    expect(touch(tile, at(8, 7))).toEqual({ colour: "#d6d8db" }); // 0.25 m across: 8 pixels
    expect(touch(tile, at(8 + 1, 3))).toBeNull();
    expect(linesDown(tile, 3, DEEP)).toEqual([7, 14, 21]); // every 0.25 m going back
  });

  it("draws the neon grid on every metre in its own colour, and fainter through the middles", () => {
    const grid: FloorLook = { color: "#12141d", roughness: 0.4, texture: "grid", lines: "#1c3d8a", metres: 1 };
    expect(touch(grid, at(ACROSS, 5))).toEqual({ colour: "#1c3d8a" });
    expect(touch(grid, at(ACROSS / 2, 5))).toEqual({ colour: "#1c3d8a", alpha: 0.35 });
    expect(touch(grid, at(5, 5))).toBeNull();
  });

  it("leaves a floor with no texture as it was baked", () => {
    expect(touch({ color: "#e6e2da", roughness: 0.45 }, at(ACROSS, DEEP))).toBeNull();
  });

  it("is the same floor every time", () => {
    const carpet: FloorLook = { color: "#cdd4d9", roughness: 0.9, texture: "carpet", metres: 1 };
    const once = Array.from({ length: 400 }, (_, i) => touch(carpet, at(i % 40, Math.floor(i / 40))));
    const again = Array.from({ length: 400 }, (_, i) => touch(carpet, at(i % 40, Math.floor(i / 40))));
    expect(again).toEqual(once);
    expect(once.filter(Boolean).length).toBeGreaterThan(0);
  });

  it.each(Object.keys(THEMES) as ThemeId[])("draws something on every textured floor in %s", (id) => {
    for (const [kind, look] of Object.entries(THEMES[id].palette.floors)) {
      if (!look.texture) continue;
      const seen = Array.from({ length: ACROSS * 2 * DEEP * 2 }, (_, i) => touch(look, at(i % (ACROSS * 2), Math.floor(i / (ACROSS * 2)))));
      expect(seen.some(Boolean), `${id} ${kind} (${look.texture})`).toBe(true);
    }
  });
});

describe("which pixels are floor", () => {
  const muji = THEMES.muji.palette;
  const cyber = THEMES.cyber.palette;

  it("knows a floor by its slot", () => {
    expect(floorKindOf("floors.ceo.color", muji)).toBe("ceo");
    expect(floorKindOf("floors.pantry.color", muji)).toBe("pantry");
    expect(floorKindOf("wall", muji)).toBeNull();
    expect(floorKindOf("deskTop", muji)).toBeNull();
  });

  it("treats a zone's trim as floor where the style has no trim — it was painted as floor", () => {
    expect(floorKindOf("zoneTrim.research", muji)).toBe("research");
    expect(floorKindOf("zoneTrim.research", cyber)).toBeNull(); // the neon style has the trim itself
  });

  it("patterns only the floor, never a wall or its outline", () => {
    // three pixels across one row: floor, wall, floor-outline; everything else floor
    const source = {
      w: 3,
      h: 40,
      rows: Array.from({ length: 40 }, () => "fwo"),
      keys: { f: ["floors.pantry.color", 3], w: ["wall", 3], o: ["floors.pantry.color", -1] } as Record<string, readonly [string, number]>,
      origin: [0, 0] as const,
    };
    const data = new Uint8ClampedArray(3 * 40 * 4).fill(200);
    const touched = patternFloors({ data, width: 3, height: 40 }, source, muji, { across: ACROSS, deep: DEEP });
    expect(touched).toBeGreaterThan(0);
    for (let row = 0; row < 40; row++) {
      expect(data[(row * 3 + 1) * 4], `wall at row ${row}`).toBe(200);
      expect(data[(row * 3 + 2) * 4], `outline at row ${row}`).toBe(200);
    }
  });
});

describe("a touch", () => {
  it("steps darker, lighter, or toward a colour — and nothing else", () => {
    expect(applyTouch([100, 100, 100], { dark: 0.5 })).toEqual([50, 50, 50]);
    expect(applyTouch([100, 100, 100], { light: 0.5 })).toEqual([178, 178, 178]);
    expect(applyTouch([0, 0, 0], { colour: "#ff0000" })).toEqual([255, 0, 0]);
    expect(applyTouch([0, 0, 0], { colour: "#ff0000", alpha: 0.5 })).toEqual([128, 0, 0]);
  });
});

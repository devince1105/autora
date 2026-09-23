// D-026: the baked sprites, and painting them in the office's styles.
//
// The bake itself needs a browser and runs by hand (``pnpm -F web bake-sprites``); what these check
// is what the board relies on afterwards — that every sprite is well-formed, and that every pixel
// has a colour in every style. A slot one style's palette lacks would be a hole in the furniture
// that appears only when somebody picks that style, which is exactly the kind of bug nobody sees.
import { describe, expect, it } from "vitest";

import { ROLE_COLOR, THEMES, type ThemeId } from "@/office3d/palette";
import { BAKEABLE } from "@/office3d/scene/furniture";

import { BAKED } from "./baked";
import { ACCENT_SLOT, OUTLINE_TONE, RAMP, neonRim, paint, paintShadow, shade, slotColour } from "./theme";

const THEME_IDS = Object.keys(THEMES) as ThemeId[];
const PIECES = Object.entries(BAKED);

const luminance = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
};

describe("the baked sprites", () => {
  it("are exactly the pieces the 3D office offers for baking", () => {
    // a piece added to furniture.ts and never baked would be missing from the board, silently
    expect(Object.keys(BAKED).sort()).toEqual(Object.keys(BAKEABLE).sort());
  });

  it.each(PIECES)("%s is a rectangle of the size it says", (_, piece) => {
    expect(piece.rows).toHaveLength(piece.h);
    expect(piece.shadow).toHaveLength(piece.h);
    for (const line of [...piece.rows, ...piece.shadow]) expect(line).toHaveLength(piece.w);
  });

  it.each(PIECES)("%s uses no character it does not define", (_, piece) => {
    const used = new Set(piece.rows.join("").replace(/\./g, ""));
    for (const key of used) expect(piece.keys, `character ${key}`).toHaveProperty(key);
  });

  it.each(PIECES)("%s stands inside its own sprite", (_, piece) => {
    expect(piece.anchor).toBeGreaterThan(0);
    expect(piece.anchor).toBeLessThanOrEqual(piece.h);
    expect(piece.origin[0]).toBeGreaterThanOrEqual(0);
    expect(piece.origin[0]).toBeLessThanOrEqual(piece.w);
  });

  it.each(PIECES)("%s has a shadow that is only where the piece is not", (_, piece) => {
    piece.shadow.forEach((line, row) => {
      [...line].forEach((mark, col) => {
        if (mark === "#") expect(piece.rows[row][col]).toBe(".");
      });
    });
  });

  it("has an outline round every piece: pixel art, not a render with the edges left soft", () => {
    for (const [name, piece] of PIECES) {
      const outlined = Object.values(piece.keys).some(([, tone]) => tone === OUTLINE_TONE);
      expect(outlined, name).toBe(true);
    }
  });
});

describe("painting them in a style", () => {
  it.each(THEME_IDS)("every pixel of every piece has a colour in %s", (id) => {
    const palette = THEMES[id].palette;
    for (const [name, piece] of PIECES) {
      const art = paint(piece, palette, ROLE_COLOR.writer);
      for (const key of Object.keys(piece.keys)) {
        expect(art.palette[key], `${name}: ${piece.keys[key][0]} in ${id}`).toMatch(/^#[0-9a-f]{6}$/);
      }
    }
  });

  it("switching style repaints the same pixels, not different ones", () => {
    const desk = BAKED.desk;
    const muji = paint(desk, THEMES.muji.palette);
    const industrial = paint(desk, THEMES.industrial.palette);
    expect(industrial.rows).toEqual(muji.rows);
    expect(industrial.palette).not.toEqual(muji.palette);
  });

  it("paints a chair's stripes in its sitter's colour", () => {
    const chair = BAKED.chair;
    const stripe = Object.entries(chair.keys).find(([, [slot, tone]]) => slot === ACCENT_SLOT && tone >= 0)?.[0];
    expect(stripe, "the chair has an accent").toBeDefined();
    const writer = paint(chair, THEMES.muji.palette, ROLE_COLOR.writer).palette[stripe!];
    const researcher = paint(chair, THEMES.muji.palette, ROLE_COLOR.researcher).palette[stripe!];
    expect(writer).not.toEqual(researcher);
  });

  it("gives a shadow only to pieces that cast one", () => {
    expect(paintShadow(BAKED.palm)).not.toBeNull();
  });
});

describe("the ramp", () => {
  it("gets lighter step by step", () => {
    const steps = RAMP.map((_, tone) => luminance(shade("#a8824f", tone)));
    for (let i = 1; i < steps.length; i++) expect(steps[i]).toBeGreaterThan(steps[i - 1]);
  });

  it("puts the outline below the darkest step", () => {
    expect(luminance(shade("#a8824f", OUTLINE_TONE))).toBeLessThan(luminance(shade("#a8824f", 0)));
  });

  it("leans shadows cool and light warm, the way a pixel artist builds a ramp", () => {
    const blueShare = (hex: string) => parseInt(hex.slice(5, 7), 16) / (parseInt(hex.slice(1, 3), 16) + 1);
    expect(blueShare(shade("#808080", 0))).toBeGreaterThan(blueShare(shade("#808080", RAMP.length - 1)));
  });
});

describe("where a colour comes from", () => {
  const palette = THEMES.muji.palette;

  it("follows a path into the style's palette", () => {
    expect(slotColour(palette, "deskTop")).toBe(palette.deskTop);
    expect(slotColour(palette, "books.2")).toBe(palette.books[2]);
    expect(slotColour(palette, "floors.base.color")).toBe(palette.floors.base.color);
  });

  it("gives the accent to whoever is sitting there", () => {
    expect(slotColour(palette, ACCENT_SLOT, "#123456")).toBe("#123456");
  });

  it("answers nothing for a slot that is not a colour, rather than guessing", () => {
    expect(slotColour(palette, "nope")).toBeUndefined();
    expect(slotColour(palette, "floors.base")).toBeUndefined();
  });

  it("outlines in neon only in the style that glows", () => {
    expect(neonRim(THEMES.muji.palette)).toBeNull();
    expect(neonRim(THEMES.cyber.palette)).toMatch(/^#[0-9a-f]{6}$/);
  });
});

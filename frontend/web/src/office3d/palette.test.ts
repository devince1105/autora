import { describe, expect, it } from "vitest";

import { DEFAULT_THEME, isThemeId, THEME_IDS, THEMES } from "./palette";
import { officeParts } from "./scene/furniture";

describe("themes (T-413)", () => {
  it("five interior styles, 日式無印 by default; ids are checked", () => {
    expect(THEME_IDS).toEqual(["muji", "wabisabi", "industrial", "google", "cyber"]);
    expect(DEFAULT_THEME).toBe("muji");
    expect([isThemeId("cyber"), isThemeId("neon"), isThemeId("toString"), isThemeId(null)]).toEqual([true, false, false, false]);
  });

  it("each theme paints the office differently", () => {
    const signatures = THEME_IDS.map((id) => officeParts(THEMES[id].palette).map((p) => p.color).join());
    expect(new Set(signatures).size).toBe(THEME_IDS.length);
  });

  it("neon: every glow colour of a theme lights some parts, and most of the office stays lit normally", () => {
    for (const id of THEME_IDS) {
      const { palette } = THEMES[id];
      const parts = officeParts(palette);
      for (const colour of palette.glow) expect(parts.some((p) => p.color === colour), `${id} ${colour}`).toBe(true);
      const glowing = parts.filter((p) => palette.glow.includes(p.color)).length;
      expect(glowing / parts.length, id).toBeLessThan(0.2);
    }
    expect(THEMES.cyber.palette.glow.length).toBeGreaterThan(0);
    for (const id of THEME_IDS.filter((t) => t !== "cyber")) expect(THEMES[id].palette.glow, id).toEqual([]);
  });

  it("building one theme leaves the default palette in place for the next build", () => {
    const before = officeParts().map((p) => p.color).join();
    officeParts(THEMES.cyber.palette);
    expect(officeParts().map((p) => p.color).join()).toBe(before);
  });
});

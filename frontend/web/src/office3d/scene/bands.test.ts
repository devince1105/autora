// T-600 batch 4: which business each room works for, as a band on its floor (§14.7).
import { describe, expect, it } from "vitest";

import { BUSINESS_COLORS, businessColors, SHARED_COLOR } from "../palette";
import { bandsFor } from "./BusinessBands";
import { ZONES } from "./layout";

const at = (zone: string | null, business: string | null) => ({
  office_zone_key: zone,
  business_unit_key: business,
});

describe("a colour per business", () => {
  it("is decided by the keys' order, so a floor does not change colour on a rename", () => {
    const colors = businessColors(["ai_media", "ai_edu", null, "ai_media"]);
    expect(colors).toEqual({ ai_edu: BUSINESS_COLORS[0], ai_media: BUSINESS_COLORS[1] });
    // the same set in another order gives the same answer
    expect(businessColors(["ai_media", "ai_edu"])).toEqual(colors);
    expect(SHARED_COLOR).toBeTruthy(); // company-wide functions have their own, not a business's
  });

  it("more businesses than colours repeats rather than inventing one", () => {
    const many = Array.from({ length: BUSINESS_COLORS.length + 2 }, (_, i) => `b${i}`);
    const colors = businessColors(many);
    expect(new Set(Object.values(colors)).size).toBe(BUSINESS_COLORS.length);
  });
});

describe("the bands on the floor", () => {
  it("one per room somebody works in, in that business's colour", () => {
    const bands = bandsFor([
      at("research", "ai_media"),
      at("editorial", "ai_media"),
      at("growth", "ai_edu"),
    ]);
    expect(bands.map((b) => [b.zone, b.business])).toEqual([
      ["editorial", "ai_media"],
      ["growth", "ai_edu"],
      ["research", "ai_media"],
    ]);
    // the two rooms of one business share a colour; the other business has its own
    const media = bands.filter((b) => b.business === "ai_media").map((b) => b.color);
    expect(new Set(media).size).toBe(1);
    expect(bands.find((b) => b.business === "ai_edu")!.color).not.toBe(media[0]);
  });

  it("a company-wide function gets no band: it is not a business", () => {
    expect(bandsFor([at("ceo", null), at("research", null)])).toEqual([]);
  });

  it("an agent with no room, or a room the floor plan does not draw, adds nothing", () => {
    expect(bandsFor([at(null, "ai_media"), at("warehouse", "ai_media")])).toEqual([]);
  });

  it("a band sits inside its room, along the edge it is entered from", () => {
    for (const band of bandsFor([at("research", "a"), at("growth", "b")])) {
      const area = ZONES[band.zone as keyof typeof ZONES];
      expect(band.at[0]).toBeGreaterThan(area.minX);
      expect(band.at[0]).toBeLessThan(area.maxX);
      expect(band.at[1]).toBeGreaterThan(area.minZ);
      expect(band.at[1]).toBeLessThan(area.maxZ);
      expect(band.width).toBeLessThan(area.maxX - area.minX);
    }
  });
});

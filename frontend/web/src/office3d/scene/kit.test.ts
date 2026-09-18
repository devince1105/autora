import { Color } from "three";
import { describe, expect, it } from "vitest";

import { officeParts, partitionGlassParts, windowGlassParts } from "./furniture";
import { block, box, buildGeometry, place, triangleCount } from "./kit";
import { ROOM } from "./layout";

describe("kit", () => {
  it("merges parts into one geometry with a colour per vertex", () => {
    const g = buildGeometry([box(1, 1, 1, [0, 0, 0], "#ff0000"), block(1, 2, 1, [3, 0, 0], "#00ff00")]);
    expect(triangleCount(g)).toBe(24);
    const colors = g.getAttribute("color");
    const red = new Color("#ff0000");
    expect([colors.getX(0), colors.getY(0), colors.getZ(0)]).toEqual([red.r, red.g, red.b]);
    const green = new Color("#00ff00");
    expect([colors.getX(36), colors.getY(36), colors.getZ(36)]).toEqual([green.r, green.g, green.b]);
    // block() stands on its y: the second box spans y 0..2
    expect(g.boundingBox!.max.y).toBeCloseTo(2);
  });

  it("place() moves and turns a piece about its origin", () => {
    const g = buildGeometry(place([box(2, 0.1, 0.2, [1, 0, 0], "#000")], 5, 5, Math.PI / 2));
    // a 2 m bar along +x, turned a quarter, now runs along -z from the new origin
    expect(g.boundingBox!.min.z).toBeCloseTo(3);
    expect(g.boundingBox!.max.z).toBeCloseTo(5, 0);
    expect(g.boundingBox!.max.x - g.boundingBox!.min.x).toBeCloseTo(0.2);
  });
});

describe("the office geometry", () => {
  const office = buildGeometry(officeParts());

  it("stays low-poly: static office under 40k triangles (04 §7 total < 50k, avatars included)", () => {
    const total = triangleCount(office) + triangleCount(buildGeometry(partitionGlassParts())) + triangleCount(buildGeometry(windowGlassParts()));
    expect(total).toBeLessThan(40_000);
    expect(total).toBeGreaterThan(5_000); // the detail is really there
  });

  it("everything sits on the slab, under the wall caps", () => {
    const b = office.boundingBox!;
    expect(b.min.x).toBeGreaterThanOrEqual(ROOM.minX - 0.45);
    expect(b.max.x).toBeLessThanOrEqual(ROOM.maxX + 0.45);
    expect(b.min.z).toBeGreaterThanOrEqual(ROOM.minZ - 0.45);
    expect(b.max.z).toBeLessThanOrEqual(ROOM.maxZ + 0.45);
    expect(b.min.y).toBeGreaterThanOrEqual(-0.36);
    expect(b.max.y).toBeLessThanOrEqual(ROOM.wallHeight + 0.1);
  });
});

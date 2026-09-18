// The floor regions (T-402, D-010): light base, dark glossy work area, yellow corridors, wood in
// the CEO office, tiles in the pantry, rugs. Planes a few millimetres apart, receiving shadows.
import { useEffect, useMemo } from "react";
import type { Texture } from "three";

import { PALETTE } from "../palette";
import { floorRegions, type FloorKind } from "./furniture";
import { tileTexture, woodTexture } from "./textures";

const LOOK: Record<FloorKind, { color: string; roughness: number; texture?: "wood" | "tile"; metres?: number }> = {
  base: { color: PALETTE.floorBase, roughness: 0.7 },
  work: { color: PALETTE.floorWork, roughness: 0.28 },
  corridor: { color: PALETTE.corridor, roughness: 0.45 },
  wood: { color: "#ffffff", roughness: 0.55, texture: "wood", metres: 1 },
  meeting: { color: "#ffffff", roughness: 0.55, texture: "wood", metres: 1 },
  tile: { color: "#ffffff", roughness: 0.35, texture: "tile", metres: 0.5 },
  rugLounge: { color: PALETTE.rugLounge, roughness: 0.95 },
  rugCeo: { color: PALETTE.rugCeo, roughness: 0.95 },
};

export function Floors() {
  const textures = useMemo(
    () => ({
      wood: woodTexture(PALETTE.woodFloor),
      meeting: woodTexture(PALETTE.meetingFloor),
      tile: tileTexture(PALETTE.tileFloor, "#d6d8db"),
    }),
    [],
  );
  const regions = useMemo(
    () =>
      floorRegions().map((region, i) => {
        const look = LOOK[region.kind];
        const width = region.maxX - region.minX;
        const depth = region.maxZ - region.minZ;
        let map: Texture | undefined;
        if (look.texture) {
          map = (region.kind === "meeting" ? textures.meeting : textures[look.texture]).clone();
          map.repeat.set(width / look.metres!, depth / look.metres!);
          map.needsUpdate = true;
        }
        return { key: `${region.kind}-${i}`, region, look, width, depth, map };
      }),
    [textures],
  );
  useEffect(
    () => () => {
      regions.forEach((r) => r.map?.dispose());
      Object.values(textures).forEach((t) => t.dispose());
    },
    [regions, textures],
  );
  return (
    <group>
      {regions.map(({ key, region, look, width, depth, map }) => (
        <mesh
          key={key}
          rotation-x={-Math.PI / 2}
          position={[(region.minX + region.maxX) / 2, 0.002 + region.layer * 0.003, (region.minZ + region.maxZ) / 2]}
          receiveShadow
        >
          <planeGeometry args={[width, depth]} />
          <meshStandardMaterial color={look.color} roughness={look.roughness} map={map} />
        </mesh>
      ))}
    </group>
  );
}

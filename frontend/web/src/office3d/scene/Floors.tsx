// The floor regions (T-402, T-413): the theme's base floor, a carpet per department, the lobby's
// stone, wood in the CEO office, tiles in the pantry, walkways, rugs and the entrance mat. Planes
// a few millimetres apart, receiving shadows.
import { useEffect, useMemo } from "react";
import type { Texture } from "three";

import type { FloorKind, Palette } from "../palette";
import { floorRegions } from "./furniture";
import { floorTexture } from "./textures";

export function Floors({ palette }: { palette: Palette }) {
  const textures = useMemo(() => {
    const out = new Map<FloorKind, Texture>();
    for (const [kind, look] of Object.entries(palette.floors) as [FloorKind, Palette["floors"][FloorKind]][]) {
      const texture = floorTexture(look);
      if (texture) out.set(kind, texture);
    }
    return out;
  }, [palette]);
  const regions = useMemo(
    () =>
      floorRegions().map((region, i) => {
        const look = palette.floors[region.kind];
        const width = region.maxX - region.minX;
        const depth = region.maxZ - region.minZ;
        const base = textures.get(region.kind);
        let map: Texture | undefined;
        if (base && look.metres) {
          map = base.clone();
          map.repeat.set(width / look.metres, depth / look.metres);
          map.needsUpdate = true;
        }
        // a textured floor is painted in its colour; the material then must not tint it again
        return { key: `${region.kind}-${i}`, region, color: map ? "#ffffff" : look.color, roughness: look.roughness, emissive: look.emissive ?? "#000000", glowMap: look.glowMap, width, depth, map };
      }),
    [palette, textures],
  );
  useEffect(
    () => () => {
      regions.forEach((r) => r.map?.dispose());
      textures.forEach((t) => t.dispose());
    },
    [regions, textures],
  );
  return (
    <group>
      {regions.map(({ key, region, color, roughness, emissive, glowMap, width, depth, map }) => (
        <mesh
          key={key}
          rotation-x={-Math.PI / 2}
          position={[(region.minX + region.maxX) / 2, 0.002 + region.layer * 0.003, (region.minZ + region.maxZ) / 2]}
          receiveShadow
        >
          <planeGeometry args={[width, depth]} />
          <meshStandardMaterial
            color={color}
            roughness={roughness}
            map={map}
            // a glowing texture (grid lines) lights itself; otherwise the look's own faint glow
            emissive={glowMap && map ? "#ffffff" : emissive}
            emissiveMap={glowMap && map ? map : undefined}
            emissiveIntensity={glowMap && map ? glowMap : 1}
          />
        </mesh>
      ))}
    </group>
  );
}

// Assembles the office (04 §1, D-010): a key light from the upper left whose shadows fall toward
// the camera (as in the reference renders), an environment baked from light panels (no download), the floors, the merged static office, glass, windows and screens, all
// fitted into the camera; the agents (T-405) with their screens, lamps and tags (T-406). The
// courier joins in T-408. The static parts, floors and lights follow the chosen theme (T-413);
// parts in a theme's neon colours go into a second, unlit mesh so they glow.
import { useEffect, useMemo } from "react";

import { Agents } from "../agents/Agents";
import { CameraRig } from "../camera/CameraRig";
import { SelectionMarker } from "../interaction/SelectionMarker";
import { DeskStatus, HeadTags } from "../agents/StatusIndicators";
import { DEFAULT_THEME, THEMES, type ThemeId } from "../palette";
import { StatsProbe } from "../perf/StatsProbe";
import { CueProvider } from "../visual/CueRunner";
import { VisualTrackerProvider } from "../visual/tracker";
import { Floors } from "./Floors";
import { OfficeEnvironment } from "./OfficeEnvironment";
import { officeParts, partitionGlassParts, windowGlassParts } from "./furniture";
import { buildGeometry } from "./kit";
import { Labels } from "./Labels";

const SHADOW_EXTENT = 17;

export function OfficeScene({ insetRight = 0, theme = DEFAULT_THEME }: { insetRight?: number; theme?: ThemeId }) {
  const palette = THEMES[theme].palette;
  const light = palette.lighting;
  const [office, neon] = useMemo(() => {
    const glow = new Set(palette.glow);
    const parts = officeParts(palette);
    const lit = parts.filter((p) => !glow.has(p.color));
    const glowing = parts.filter((p) => glow.has(p.color));
    return [buildGeometry(lit), glowing.length ? buildGeometry(glowing) : null];
  }, [palette]);
  const glass = useMemo(() => buildGeometry(partitionGlassParts(palette)), [palette]);
  const windows = useMemo(() => buildGeometry(windowGlassParts(palette)), [palette]);
  useEffect(
    () => () => {
      office.dispose();
      neon?.dispose();
      glass.dispose();
      windows.dispose();
    },
    [office, neon, glass, windows],
  );

  return (
    <>
      <hemisphereLight args={[light.sky, light.ground, light.hemisphere]} />
      <directionalLight
        castShadow
        position={[-12, 22, 5]}
        color={light.key}
        intensity={light.keyIntensity}
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-SHADOW_EXTENT}
        shadow-camera-right={SHADOW_EXTENT}
        shadow-camera-top={SHADOW_EXTENT}
        shadow-camera-bottom={-SHADOW_EXTENT}
        shadow-camera-near={1}
        shadow-camera-far={60}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
      />
      {/* light panels for reflections and fill, baked once (not drei's portal: it leaked) */}
      <OfficeEnvironment intensity={light.environment} />

      <group>
        {/* keyed by theme: a material gaining a map it did not have needs a new shader */}
        <Floors key={theme} palette={palette} />
        <Labels palette={palette} />
        <mesh geometry={office} castShadow receiveShadow>
          <meshStandardMaterial vertexColors roughness={0.6} />
        </mesh>
        {neon ? (
          <mesh geometry={neon} castShadow>
            <meshBasicMaterial vertexColors toneMapped={false} />
          </mesh>
        ) : null}
      </group>
      <mesh geometry={windows}>
        <meshStandardMaterial vertexColors emissive={light.windowGlow} emissiveIntensity={light.windowGlowIntensity} roughness={0.1} />
      </mesh>
      <mesh geometry={glass} renderOrder={1}>
        <meshStandardMaterial vertexColors transparent opacity={0.22} roughness={0.05} metalness={0.1} depthWrite={false} />
      </mesh>
      <VisualTrackerProvider>
        <CueProvider>
          <DeskStatus />
          <Agents />
          <HeadTags />
          <SelectionMarker />
        </CueProvider>
      </VisualTrackerProvider>
      <CameraRig insetRight={insetRight} />
      <StatsProbe />
    </>
  );
}

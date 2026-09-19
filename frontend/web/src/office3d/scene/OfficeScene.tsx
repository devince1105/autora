// Assembles the office (04 §1, D-010): a key light from the upper left whose shadows fall toward
// the camera (as in the reference renders), an environment baked from light panels (no download), the floors, the merged static office, glass, windows and screens, all
// fitted into the camera; the agents (T-405) with their screens, lamps and tags (T-406). The
// courier joins in T-408.
import { useEffect, useMemo } from "react";

import { Agents } from "../agents/Agents";
import { CameraRig } from "../camera/CameraRig";
import { SelectionMarker } from "../interaction/SelectionMarker";
import { DeskStatus, HeadTags } from "../agents/StatusIndicators";
import { StatsProbe } from "../perf/StatsProbe";
import { CueProvider } from "../visual/CueRunner";
import { VisualTrackerProvider } from "../visual/tracker";
import { Floors } from "./Floors";
import { OfficeEnvironment } from "./OfficeEnvironment";
import { officeParts, partitionGlassParts, windowGlassParts } from "./furniture";
import { buildGeometry } from "./kit";

const SHADOW_EXTENT = 17;

export function OfficeScene({ insetRight = 0 }: { insetRight?: number }) {
  const office = useMemo(() => buildGeometry(officeParts()), []);
  const glass = useMemo(() => buildGeometry(partitionGlassParts()), []);
  const windows = useMemo(() => buildGeometry(windowGlassParts()), []);
  useEffect(
    () => () => {
      office.dispose();
      glass.dispose();
      windows.dispose();
    },
    [office, glass, windows],
  );

  return (
    <>
      <hemisphereLight args={["#ffffff", "#cfc8bd", 0.7]} />
      <directionalLight
        castShadow
        position={[-12, 22, 5]}
        intensity={3.2}
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
      <OfficeEnvironment />

      <group>
        <Floors />
        <mesh geometry={office} castShadow receiveShadow>
          <meshStandardMaterial vertexColors roughness={0.6} />
        </mesh>
      </group>
      <mesh geometry={windows}>
        <meshStandardMaterial vertexColors emissive="#e8f6ff" emissiveIntensity={0.55} roughness={0.1} />
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

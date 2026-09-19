"use client";

// The WebGL part of the office, loaded only on the client (next/dynamic, ssr: false) and only
// when 3D was chosen: the canvas, camera and context-loss wiring; the scene is OfficeScene.
import { Canvas } from "@react-three/fiber";
import { NeutralToneMapping } from "three";

import { uiStore } from "@/stores/ui";

import type { ThemeId } from "./palette";
import { OfficeScene } from "./scene/OfficeScene";

export const CANVAS_DPR: [number, number] = [1, 1.5];
/** The camera starts isometric; CameraRig (T-409) frames the room and takes it from there. */
const CAMERA_POSITION: [number, number, number] = [40, 46, 40];

export interface Canvas3DProps {
  frameloop: "always" | "never";
  /** Pixels covered on the right while an agent is selected (the detail panel). */
  insetRight?: number;
  /** The office's look (T-413). */
  theme?: ThemeId;
  onContextLost: () => void;
  onContextRestored: () => void;
}

export default function Canvas3D({ frameloop, insetRight, theme, onContextLost, onContextRestored }: Canvas3DProps) {
  return (
    <Canvas
      dpr={CANVAS_DPR}
      orthographic
      // PCF (three removed the soft variant and warned on every shader compile)
      shadows="percentage"
      frameloop={frameloop}
      camera={{ position: CAMERA_POSITION, zoom: 30, near: 0.1, far: 500 }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
      // a click on nothing (not a drag) clears the selection
      onPointerMissed={() => uiStore.getState().selectAgent(null)}
      onCreated={({ gl }) => {
        // Neutral tone mapping keeps the palette's colours (filmic ACES greys them out).
        gl.toneMapping = NeutralToneMapping;
        const canvas = gl.domElement;
        canvas.addEventListener("webglcontextlost", (event) => {
          event.preventDefault(); // allow a restore instead of a dead canvas
          onContextLost();
        });
        canvas.addEventListener("webglcontextrestored", onContextRestored);
      }}
    >
      <OfficeScene insetRight={insetRight} theme={theme} />
    </Canvas>
  );
}

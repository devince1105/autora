"use client";

// The WebGL part of the office, loaded only on the client (next/dynamic, ssr: false) and only
// when 3D was chosen: the canvas, camera and context-loss wiring; the scene is OfficeScene.
import { Canvas, useThree } from "@react-three/fiber";
import { useEffect } from "react";
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

/** Report this canvas losing its context — and stop reporting the moment it is taken down.
 *
 * Tearing the canvas down *is* a lost context as far as the browser is concerned, and the event
 * arrives after React has moved on: switching to the 2D board and back used to put "3D 暫停"
 * over a canvas that had only just been built, because the dying one was still being listened
 * to. Listening from inside the canvas gives the listener the canvas's own lifetime.
 */
function ContextWatch({ onLost, onRestored }: { onLost: () => void; onRestored: () => void }) {
  const gl = useThree((state) => state.gl);
  useEffect(() => {
    const canvas = gl.domElement;
    let alive = true;
    const lost = (event: Event) => {
      event.preventDefault(); // allow a restore instead of a dead canvas
      if (alive) onLost();
    };
    const restored = () => {
      if (alive) onRestored();
    };
    canvas.addEventListener("webglcontextlost", lost);
    canvas.addEventListener("webglcontextrestored", restored);
    return () => {
      alive = false;
      canvas.removeEventListener("webglcontextlost", lost);
      canvas.removeEventListener("webglcontextrestored", restored);
    };
  }, [gl, onLost, onRestored]);
  return null;
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
      }}
    >
      <ContextWatch onLost={onContextLost} onRestored={onContextRestored} />
      <OfficeScene insetRight={insetRight} theme={theme} />
    </Canvas>
  );
}

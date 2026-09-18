"use client";

// The WebGL part of the office, loaded only on the client (next/dynamic, ssr: false) and only
// when 3D was chosen: the canvas, camera and context-loss wiring; the scene is OfficeScene.
import { Canvas } from "@react-three/fiber";
import { NeutralToneMapping } from "three";

import { OfficeScene } from "./scene/OfficeScene";

export const CANVAS_DPR: [number, number] = [1, 1.5];
/**
 * Isometric view from the front right (D-010): an orthographic camera along (1, 1.15, 1); the
 * scene's <Bounds> fits the room into the canvas and refits on resize (CameraRig in T-409).
 */
const CAMERA_POSITION: [number, number, number] = [40, 46, 40];

export interface Canvas3DProps {
  frameloop: "always" | "never";
  onContextLost: () => void;
  onContextRestored: () => void;
}

export default function Canvas3D({ frameloop, onContextLost, onContextRestored }: Canvas3DProps) {
  return (
    <Canvas
      dpr={CANVAS_DPR}
      orthographic
      shadows
      frameloop={frameloop}
      camera={{ position: CAMERA_POSITION, zoom: 30, near: 0.1, far: 500 }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
      onCreated={({ gl, camera }) => {
        camera.lookAt(0, 0, 0);
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
      <OfficeScene />
    </Canvas>
  );
}

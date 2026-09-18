"use client";

// The WebGL part of the office, loaded only on the client (next/dynamic, ssr: false) and only
// when 3D was chosen: the canvas, camera and context-loss wiring; the scene is OfficeScene.
import { Canvas } from "@react-three/fiber";

import { OfficeScene } from "./scene/OfficeScene";

export const CANVAS_DPR: [number, number] = [1, 1.5];
/** Default view: from the front right, high, the whole room in frame (CameraRig in T-409). */
const CAMERA_POSITION: [number, number, number] = [15, 17, 19];
const CAMERA_TARGET: [number, number, number] = [-0.5, 0, 0.5];

export interface Canvas3DProps {
  frameloop: "always" | "never";
  onContextLost: () => void;
  onContextRestored: () => void;
}

export default function Canvas3D({ frameloop, onContextLost, onContextRestored }: Canvas3DProps) {
  return (
    <Canvas
      dpr={CANVAS_DPR}
      // No filmic tone mapping: it greys out the pastel palette (D-008); colours show as picked.
      flat
      frameloop={frameloop}
      camera={{ position: CAMERA_POSITION, fov: 36, near: 0.1, far: 200 }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
      onCreated={({ gl, camera }) => {
        camera.lookAt(...CAMERA_TARGET);
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

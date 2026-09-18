"use client";

// The WebGL part of the office, loaded only on the client (next/dynamic, ssr: false) and only
// when 3D was chosen. T-401 sets up the canvas; the room, desks and avatars come in T-402+.
import { Canvas } from "@react-three/fiber";

import { PALETTE } from "./palette";

export const CANVAS_DPR: [number, number] = [1, 1.5];

export interface Canvas3DProps {
  frameloop: "always" | "never";
  onContextLost: () => void;
  onContextRestored: () => void;
}

export default function Canvas3D({ frameloop, onContextLost, onContextRestored }: Canvas3DProps) {
  return (
    <Canvas
      dpr={CANVAS_DPR}
      frameloop={frameloop}
      camera={{ position: [16, 15, 16], fov: 40, near: 0.1, far: 200 }}
      gl={{ antialias: true, powerPreference: "high-performance" }}
      onCreated={({ gl, camera }) => {
        camera.lookAt(0, 0, 0);
        const canvas = gl.domElement;
        canvas.addEventListener("webglcontextlost", (event) => {
          event.preventDefault(); // allow a restore instead of a dead canvas
          onContextLost();
        });
        canvas.addEventListener("webglcontextrestored", onContextRestored);
      }}
    >
      <color attach="background" args={[PALETTE.sky]} />
      <hemisphereLight args={[PALETTE.skyLight, PALETTE.groundLight, 1.1]} />
      <directionalLight position={[8, 14, 6]} intensity={1.4} />
      <mesh rotation-x={-Math.PI / 2} receiveShadow>
        <planeGeometry args={[20, 14]} />
        <meshStandardMaterial color={PALETTE.floor} />
      </mesh>
    </Canvas>
  );
}

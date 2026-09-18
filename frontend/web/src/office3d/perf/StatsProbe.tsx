// A tiny in-canvas probe (T-402, used by T-412): once a second it records frames per second, draw
// calls and triangles of the last frame on window.__autoraOffice, so browser tests can assert
// on them without a visible overlay. Costs one object write per second.
import { useFrame } from "@react-three/fiber";
import { useRef } from "react";

export interface OfficeStats {
  fps: number;
  drawCalls: number;
  triangles: number;
  frames: number;
  at: number;
}

declare global {
  interface Window {
    __autoraOffice?: OfficeStats;
  }
}

export function StatsProbe() {
  const sample = useRef({ start: 0, frames: 0, total: 0 });
  useFrame(({ gl }) => {
    const now = performance.now();
    const w = sample.current;
    if (w.start === 0) w.start = now;
    w.frames += 1;
    w.total += 1;
    if (now - w.start >= 1000) {
      window.__autoraOffice = {
        fps: Math.round((w.frames * 1000) / (now - w.start)),
        drawCalls: gl.info.render.calls,
        triangles: gl.info.render.triangles,
        frames: w.total,
        at: Date.now(),
      };
      w.start = now;
      w.frames = 0;
    }
  });
  return null;
}

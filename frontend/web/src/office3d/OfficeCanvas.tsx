"use client";

// The office's only public component (features import nothing else from office3d). It decides
// 3D or 2D once on the client, loads the WebGL part lazily, stops drawing while the tab is
// hidden, and turns a lost WebGL context into an overlay with a manual rebuild (no automatic
// retry loop) — 3d-office/04 §7.
import dynamic from "next/dynamic";
import { useEffect, useState, type ComponentType } from "react";

import type { Canvas3DProps } from "./Canvas3D";
import { chooseMode, detectCapabilities, type Capabilities, type ModeReason, type OfficeView } from "./capabilities";
import { OfficeBoard2D } from "./fallback/OfficeBoard2D";
import { onOfficeKey } from "./interaction/picking";
import { usePageVisible } from "./usePageVisible";

export type { OfficeView } from "./capabilities";
export { parseView } from "./capabilities";

const Loading = () => <p className="p-4 text-sm text-muted">載入 3D 辦公室…</p>;

const LazyCanvas3D = dynamic(() => import("./Canvas3D"), { ssr: false, loading: Loading });

const REASON: Record<ModeReason, string | null> = {
  selected: null,
  auto: null,
  "no-webgl2": "這個瀏覽器不支援 WebGL 2，改用 2D 看板。",
  narrow: "螢幕較窄，改用 2D 看板（可切換到 3D）。",
};

export interface OfficeCanvasProps {
  view?: OfficeView;
  onViewChange?: (view: OfficeView) => void;
  /** Pixels on the right the page covers while an agent is selected (its detail panel). */
  selectionInsetRight?: number;
  /** Test seams: capability probe and the WebGL scene. */
  detect?: () => Capabilities;
  Scene?: ComponentType<Canvas3DProps>;
}

export function OfficeCanvas({
  view = "auto",
  onViewChange,
  selectionInsetRight = 0,
  detect = detectCapabilities,
  Scene = LazyCanvas3D,
}: OfficeCanvasProps) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [lost, setLost] = useState(false);
  const [generation, setGeneration] = useState(0);
  const visible = usePageVisible();

  // Esc clears the selection, 1–6 pick a role: in 3D and on the 2D board alike
  useEffect(() => {
    window.addEventListener("keydown", onOfficeKey);
    return () => window.removeEventListener("keydown", onOfficeKey);
  }, []);

  useEffect(() => {
    // Once, on the client (no window during server rendering); probing again changes nothing.
    setCaps(detect());
  }, []);

  if (!caps) return <div data-office-mode="detecting" className="h-full" />;
  const { mode, reason } = chooseMode(caps, view);

  if (mode === "2d") {
    return (
      <div data-office-mode="2d" className="h-full overflow-y-auto">
        {REASON[reason] ? <p className="px-4 pt-4 text-sm text-muted">{REASON[reason]}</p> : null}
        <OfficeBoard2D />
      </div>
    );
  }

  return (
    <div
      data-office-mode="3d"
      data-context-lost={lost}
      className="relative h-full bg-[linear-gradient(180deg,#cdd3dc_0%,#97a2b2_100%)]"
    >
      <Scene
        key={generation}
        frameloop={visible && !lost ? "always" : "never"}
        insetRight={selectionInsetRight}
        onContextLost={() => setLost(true)}
        onContextRestored={() => setLost(false)}
      />
      {lost ? (
        <div
          role="alert"
          className="absolute inset-0 grid place-content-center gap-3 bg-canvas/85 text-center"
        >
          <p className="font-medium">3D 暫停：瀏覽器收回了繪圖資源（WebGL context）。</p>
          <div className="flex justify-center gap-2">
            <button
              type="button"
              onClick={() => {
                setLost(false);
                setGeneration((n) => n + 1);
              }}
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-canvas"
            >
              重新建立 3D
            </button>
            {onViewChange ? (
              <button
                type="button"
                onClick={() => onViewChange("2d")}
                className="rounded-lg border border-line px-4 py-1.5 text-sm"
              >
                改用 2D 看板
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

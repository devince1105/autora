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
import { useRoster } from "./agents/roster";
import { onOfficeKey } from "./interaction/picking";
import { THEMES } from "./palette";
import { OfficeSettings } from "./OfficeSettings";
import { useOfficeTheme } from "./theme";
import { usePageVisible } from "./usePageVisible";

export type { OfficeView } from "./capabilities";
export { parseView } from "./capabilities";
/** The names the office puts on its rooms, for pages that offer a way into them (T-600). */
export { DEPARTMENT_LABEL } from "./fallback/board";
/** The colour a business is marked with on the floor, for pages that list them (T-600). */
export { businessColors } from "./palette";

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
  /** key -> name for the company's departments, from the org chart (T-600 batch 3). */
  departmentNames?: Readonly<Record<string, string>>;
  /** Test seams: capability probe and the WebGL scene. */
  detect?: () => Capabilities;
  Scene?: ComponentType<Canvas3DProps>;
}

export function OfficeCanvas({
  view = "auto",
  onViewChange,
  departmentNames,
  selectionInsetRight = 0,
  detect = detectCapabilities,
  Scene = LazyCanvas3D,
}: OfficeCanvasProps) {
  const empty = useRoster().members.length === 0;
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [lost, setLost] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [theme, setTheme] = useOfficeTheme();
  const [settingsOpen, setSettingsOpen] = useState(false);
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

  const decision = caps ? chooseMode(caps, view) : null;

  useEffect(() => {
    // Handing over to the board destroys the 3D canvas, and the browser reports that the only
    // way it can: a lost context. That is this canvas ending, not the next one failing, so the
    // flag is cleared whenever 3D is not on screen — coming back builds a fresh one.
    if (decision?.mode !== "3d") setLost(false);
  }, [decision?.mode]);

  if (!caps || !decision) return <div data-office-mode="detecting" className="h-full" />;

  const { mode, reason } = decision;

  if (mode === "2d") {
    return (
      <div data-office-mode="2d" className="h-full overflow-y-auto">
        {REASON[reason] ? <p className="px-4 pt-4 text-sm text-muted">{REASON[reason]}</p> : null}
        <OfficeBoard2D departmentNames={departmentNames} />
      </div>
    );
  }

  const [top, bottom] = THEMES[theme].palette.backdrop;
  return (
    <div
      data-office-mode="3d"
      data-context-lost={lost}
      data-office-theme={theme}
      className="relative h-full"
      style={{ background: `linear-gradient(180deg, ${top} 0%, ${bottom} 100%)` }}
    >
      <Scene
        key={generation}
        frameloop={visible && !lost ? "always" : "never"}
        insetRight={selectionInsetRight}
        theme={theme}
        onContextLost={() => setLost(true)}
        onContextRestored={() => setLost(false)}
      />
      <OfficeSettings theme={theme} onTheme={setTheme} open={settingsOpen} onOpen={setSettingsOpen} />
      {empty ? (
        <div
          role="status"
          className="absolute inset-x-0 top-1/2 mx-auto w-fit rounded-lg border border-line bg-surface/90 px-4 py-2 text-sm text-muted shadow-sm"
        >
          這間公司還沒有代理。{" "}
          <a href="/agents" className="text-accent underline">
            去雇用
          </a>
        </div>
      ) : null}
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

"use client";

// The office's only public component (features import nothing else from office3d). It decides
// 3D or 2D once on the client, loads the WebGL part lazily, stops drawing while the tab is
// hidden, and turns a lost WebGL context into an overlay with a manual rebuild (no automatic
// retry loop) — 3d-office/04 §7.
import dynamic from "next/dynamic";
import { useEffect, useRef, useState, type ComponentType } from "react";

import type { Canvas3DProps } from "./Canvas3D";
import { chooseMode, detectCapabilities, type Capabilities, type ModeReason, type OfficeView } from "./capabilities";
import { useRoster } from "./agents/roster";
import { onOfficeKey } from "./interaction/picking";
import { THEMES } from "./palette";
import { OfficeSettings } from "./OfficeSettings";
import { terminalVars } from "./fallback/console";
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

const Loading2D = () => <p className="p-4 text-sm text-muted">載入 2D 看板…</p>;

/**
 * The 2D board, loaded only when it is shown: its baked pictures — the office's shell, its
 * furniture and twelve characters in every pose (D-027) — are most of a hundred kilobytes that
 * nobody looking at the 3D office should have to download.
 */
const LazyBoard2D = dynamic(() => import("./fallback/OfficeBoard2D").then((m) => m.OfficeBoard2D), {
  ssr: false,
  loading: Loading2D,
});

/**
 * Fetch the 2D board ahead of need: the same module the lazy board loads, so the same chunk, and a
 * later switch to 2D finds it already there. The user chose this: a switch to 2D while the 3D
 * office is still starting up should not also wait for the board to download — at the cost of
 * everybody fetching it once, whether they look at 2D or not.
 */
export const preloadBoard2D = (): void => {
  void import("./fallback/OfficeBoard2D");
};

/** How long to wait for the page to have a quiet moment before fetching anyway. */
const IDLE_TIMEOUT_MS = 4000;
/** Where the browser has no idle callbacks (Safari): a plain delay, long enough to be after start-up. */
const IDLE_FALLBACK_MS = 1500;

/** Run ``work`` when the page is quiet — after the 3D office's heavy first frames, not during them. */
function whenIdle(work: () => void): () => void {
  if (typeof window.requestIdleCallback === "function") {
    const id = window.requestIdleCallback(() => work(), { timeout: IDLE_TIMEOUT_MS });
    return () => window.cancelIdleCallback(id);
  }
  const id = window.setTimeout(work, IDLE_FALLBACK_MS);
  return () => window.clearTimeout(id);
}

const REASON: Record<ModeReason, string | null> = {
  selected: null,
  auto: null,
  "no-webgl2": "這個瀏覽器不支援 WebGL 2，改用 2D 看板。",
  narrow: "螢幕較窄，改用 2D 看板（可切換到 3D）。",
};

export interface OfficeCanvasProps {
  view?: OfficeView;
  onViewChange?: (view: OfficeView) => void;
  /** Which view is actually on screen once the browser has been asked (``auto`` resolves here).
   * The page uses it to dress itself to match — it cannot work this out on its own. */
  onMode?: (mode: "2d" | "3d") => void;
  /** Pixels on the right the page covers while an agent is selected (its detail panel). */
  selectionInsetRight?: number;
  /** key -> name for the company's departments, from the org chart (T-600 batch 3). */
  departmentNames?: Readonly<Record<string, string>>;
  /** Test seams: capability probe, the WebGL scene, and fetching the 2D board ahead of need. */
  detect?: () => Capabilities;
  Scene?: ComponentType<Canvas3DProps>;
  preload?: () => void;
}

export function OfficeCanvas({
  view = "auto",
  onViewChange,
  onMode,
  departmentNames,
  selectionInsetRight = 0,
  detect = detectCapabilities,
  Scene = LazyCanvas3D,
  preload = preloadBoard2D,
}: OfficeCanvasProps) {
  const empty = useRoster().members.length === 0;
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [generation, setGeneration] = useState(0);
  const [lostAt, setLostAt] = useState<number | null>(null);
  /** Which canvas reported the loss: a report from one that is gone is not about this one. */
  const lost = lostAt === generation;
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
    if (decision) onMode?.(decision.mode);
  }, [decision?.mode, onMode]);

  // In 3D, fetch the 2D board once the page is quiet, so switching to it is instant. In 2D it is
  // already loading; and once fetched it stays fetched, so this asks at most once per visit.
  const preloaded = useRef(false);
  useEffect(() => {
    if (decision?.mode !== "3d" || preloaded.current) return;
    return whenIdle(() => {
      preloaded.current = true;
      preload();
    });
  }, [decision?.mode, preload]);

  useEffect(() => {
    // Handing over to the board destroys the 3D canvas, and the browser reports that the only
    // way it can: a lost context, delivered after React has moved on. Leaving 3D gives the next
    // visit its own number, so a report from the canvas that just died lands on nothing.
    if (decision?.mode !== "3d") setGeneration((n) => n + 1);
  }, [decision?.mode]);

  if (!caps || !decision) return <div data-office-mode="detecting" className="h-full" />;

  const { mode, reason } = decision;

  if (mode === "2d") {
    // the 2D floor is baked from the 3D office and painted in its style (D-027), so the style
    // setting belongs here too: it is the same office, seen without WebGL
    return (
      <div data-office-mode="2d" data-office-theme={theme} className="relative h-full overflow-y-auto">
        {REASON[reason] ? <p className="px-4 pt-4 text-sm text-muted">{REASON[reason]}</p> : null}
        <LazyBoard2D departmentNames={departmentNames} theme={theme} />
        <OfficeSettings theme={theme} onTheme={setTheme} open={settingsOpen} onOpen={setSettingsOpen} />
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
        onContextLost={() => setLostAt(generation)}
        onContextRestored={() => setLostAt(null)}
      />
      <OfficeSettings theme={theme} onTheme={setTheme} open={settingsOpen} onOpen={setSettingsOpen} />
      {empty ? (
        <div
          role="status"
          className="absolute inset-x-0 top-1/2 mx-auto w-fit rounded-lg border border-line bg-surface/90 px-4 py-2 text-sm text-muted shadow-sm"
        >
          這間公司還沒有代理。{" "}
          <a href="/admin/agents" className="text-accent underline">
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
              onClick={() => setGeneration((n) => n + 1)}
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

// The office's public door (eslint boundaries): the page dresses itself in the 2D office's
// colours while that view is on screen, and this is how it reaches them.
export { terminalVars };

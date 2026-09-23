// The office's settings, behind a gear (T-413). The look is a per-viewer choice somebody makes
// once and then forgets about, so it does not deserve a row of buttons across the office all day.
//
// A plain dialog: Escape closes it, so does the backdrop and the close button, and focus starts
// inside it. It stays a client-side preference — nothing here is sent anywhere.
"use client";

import { useEffect, useRef } from "react";

import { THEMES, THEME_IDS, type ThemeId } from "./palette";

export function OfficeSettings({
  theme,
  onTheme,
  open,
  onOpen,
}: {
  theme: ThemeId;
  onTheme: (theme: ThemeId) => void;
  open: boolean;
  onOpen: (open: boolean) => void;
}) {
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLElement>("button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpen]);

  return (
    <>
      <button
        type="button"
        aria-label="辦公室設定"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => onOpen(!open)}
        className="absolute top-3 left-3 rounded-lg border border-line bg-surface/85 p-1.5 text-muted shadow-sm hover:text-fg"
      >
        {/* a gear, drawn rather than fetched */}
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8">
          <circle cx="12" cy="12" r="3.2" />
          <path d="M19.4 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.2a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.2a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.2a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.2a1.6 1.6 0 0 0-1.4 1z" />
        </svg>
      </button>

      {open ? (
        <div className="absolute inset-0 z-10 grid place-content-center bg-canvas/60">
          {/* the backdrop closes it; it is not the only way out, so it needs no role of its own */}
          <button type="button" aria-hidden tabIndex={-1} className="absolute inset-0 cursor-default" onClick={() => onOpen(false)} />
          <div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-label="辦公室設定"
            className="relative w-80 rounded-xl border border-line bg-surface p-5 shadow-lg"
          >
            <div className="flex items-baseline justify-between">
              <h2 className="text-base font-semibold">辦公室設定</h2>
              <button type="button" aria-label="關閉" onClick={() => onOpen(false)} className="text-muted hover:text-fg">
                ✕
              </button>
            </div>

            <div role="group" aria-label="辦公室風格" className="mt-4">
              <p className="text-xs text-muted">風格</p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {THEME_IDS.map((id) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={theme === id}
                    onClick={() => onTheme(id)}
                    className={`rounded-lg border px-3 py-2 text-sm ${
                      theme === id ? "border-accent bg-accent text-canvas" : "border-line text-muted hover:text-fg"
                    }`}
                  >
                    {THEMES[id].label}
                  </button>
                ))}
              </div>
              <p className="mt-3 text-xs text-muted">只記在這個瀏覽器，不會送到伺服器。</p>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

"use client";

import { useEffect, useState } from "react";

import { words, type Lang } from "./i18n";
import { applyTheme, currentTheme, savedTheme, type Theme } from "./theme";

function root(): HTMLElement | null {
  return document.querySelector<HTMLElement>("[data-site]");
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** One button: shows a moon in light mode (go dark) and a sun in dark mode (go light). */
export function ThemeToggle({ lang }: { lang: Lang }) {
  const w = words(lang);
  // unknown on the server: rendered as an empty button of the same size, so nothing moves
  const [theme, setTheme] = useState<Theme | null>(null);
  useEffect(() => {
    // the page-load script sets the pick; a site reached by navigating in from elsewhere in the
    // app never ran it
    const saved = savedTheme(storage());
    if (saved) root()?.setAttribute("data-theme", saved);
    setTheme(currentTheme(root(), window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false));
  }, []);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      aria-label={theme ? (next === "dark" ? w.toDark : w.toLight) : undefined}
      title={theme ? (next === "dark" ? w.toDark : w.toLight) : undefined}
      disabled={!theme}
      onClick={() => {
        applyTheme(root(), next, storage());
        setTheme(next);
      }}
      className="grid size-9 place-items-center rounded-md text-muted hover:bg-canvas hover:text-ink print:hidden"
    >
      {theme === "dark" ? <Sun /> : theme === "light" ? <Moon /> : null}
    </button>
  );
}

const ICON = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
} as const;

function Sun() {
  return (
    <svg {...ICON}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  );
}

function Moon() {
  return (
    <svg {...ICON}>
      <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
    </svg>
  );
}

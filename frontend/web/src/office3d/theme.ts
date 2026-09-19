// The office's look (T-413): which palette the 3D scene is painted in. A per-viewer convenience,
// remembered in this browser (localStorage); where storage is unavailable, the default.
import { useCallback, useState } from "react";

import { DEFAULT_THEME, isThemeId, type ThemeId } from "./palette";

export const THEME_STORAGE_KEY = "autora.officeTheme";

export function readTheme(): ThemeId {
  try {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeId(stored) ? stored : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function useOfficeTheme(): [ThemeId, (theme: ThemeId) => void] {
  const [theme, setTheme] = useState<ThemeId>(readTheme);
  const choose = useCallback((next: ThemeId) => {
    setTheme(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // not remembered; the choice still applies until the page is left
    }
  }, []);
  return [theme, choose];
}

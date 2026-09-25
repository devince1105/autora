// Light or dark on the public site (D-047). Nothing is stored until the reader picks one: until
// then the site follows the system. The pick is kept in this browser only.
export type Theme = "light" | "dark";

export const THEME_KEY = "aisiwhale.theme";

/** Run inline as the site root's first child, before anything is painted: the reader's pick is
 * on the page from the first frame, so a dark-mode reader never sees a white flash. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.currentScript.parentElement.setAttribute("data-theme",t)}catch(e){}`;

/** The reader's saved pick, if any. */
export function savedTheme(store: Pick<Storage, "getItem"> | null): Theme | null {
  try {
    const value = store?.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** What the page shows now: the reader's pick, or the system's preference. */
export function currentTheme(root: HTMLElement | null, prefersDark: boolean): Theme {
  const picked = root?.getAttribute("data-theme");
  if (picked === "light" || picked === "dark") return picked;
  return prefersDark ? "dark" : "light";
}

export function applyTheme(root: HTMLElement | null, theme: Theme, store: Pick<Storage, "setItem"> | null) {
  root?.setAttribute("data-theme", theme);
  try {
    store?.setItem(THEME_KEY, theme);
  } catch {
    // private windows and blocked storage: it still changes, just not for next time
  }
}

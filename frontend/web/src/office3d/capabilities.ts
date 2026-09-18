// Whether this browser gets the 3D office or the 2D board (T-401, 04 §7): WebGL 2 is required,
// and narrow screens (phones) get the board unless 3D is asked for explicitly.

export type OfficeView = "auto" | "3d" | "2d";
export type RenderMode = "3d" | "2d";

export interface Capabilities {
  webgl2: boolean;
  narrow: boolean;
}

export type ModeReason = "selected" | "no-webgl2" | "narrow" | "auto";

export const NARROW_QUERY = "(max-width: 768px)";

export function chooseMode(caps: Capabilities, view: OfficeView): { mode: RenderMode; reason: ModeReason } {
  if (view === "2d") return { mode: "2d", reason: "selected" };
  if (!caps.webgl2) return { mode: "2d", reason: "no-webgl2" }; // even when 3D is asked for
  if (view === "3d") return { mode: "3d", reason: "selected" };
  if (caps.narrow) return { mode: "2d", reason: "narrow" };
  return { mode: "3d", reason: "auto" };
}

/** Probe once on the client. The probe context is released right away. */
export function detectCapabilities(win: Window = window): Capabilities {
  let webgl2 = false;
  try {
    const gl = win.document.createElement("canvas").getContext("webgl2");
    webgl2 = gl !== null && gl !== undefined;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
  } catch {
    webgl2 = false;
  }
  return { webgl2, narrow: win.matchMedia?.(NARROW_QUERY).matches ?? false };
}

export function parseView(value: string | null): OfficeView {
  return value === "3d" || value === "2d" ? value : "auto";
}

// How the status shows on a desk and over a head (T-406, 02 §7): screen glow, desk lamp and the
// head tag's badge. Pure: colours for a visual state at time t (blinking and pulsing are clock
// based, so they need no state).
import { Color } from "three";

import type { BadgeTone, DeskLight, Screen, VisualState } from "../visual/mapping";

export const SCREEN_COLOR: Record<Screen, string> = {
  off: "#15171b",
  dim: "#2c3a50",
  active: "#7cc4ff",
  alert: "#ffb547",
};

export const LAMP_COLOR: Record<DeskLight, string> = {
  off: "#5b606a",
  on: "#fff1c9",
  blink_amber: "#ffae1f",
  blink_red: "#ff4a4a",
};

/** Blink periods (s): amber asks for attention, red is urgent. */
export const BLINK = { blink_amber: 1.2, blink_red: 0.6 } as const;

/** 0..1, smooth, `period` seconds. */
export const pulse = (t: number, period: number) => 0.5 + 0.5 * Math.sin((2 * Math.PI * t) / period);

export function screenColor(screen: Screen, t: number, out = new Color()): Color {
  out.set(SCREEN_COLOR[screen]);
  if (screen === "active") out.multiplyScalar(0.9 + 0.1 * pulse(t, 0.35)); // a working screen flickers a little
  if (screen === "alert") out.multiplyScalar(0.4 + 0.6 * pulse(t, BLINK.blink_amber));
  return out;
}

/** The lamp shade's colour and the glow it throws on the desk (black = no glow). */
export function lampColors(light: DeskLight, t: number, shade = new Color(), glow = new Color()): { shade: Color; glow: Color } {
  shade.set(LAMP_COLOR[light]);
  const strength =
    light === "off" ? 0 : light === "on" ? 0.6 : pulse(t, BLINK[light]) * 1.4;
  if (light !== "off" && light !== "on") shade.multiplyScalar(0.35 + 0.65 * pulse(t, BLINK[light]));
  glow.set(LAMP_COLOR[light]).multiplyScalar(strength);
  return { shade, glow };
}

export const TONE_CLASS: Record<BadgeTone, string> = {
  muted: "bg-neutral/20 text-muted",
  info: "bg-accent/15 text-accent",
  active: "bg-ok/15 text-ok",
  warn: "bg-warn/25 text-warn",
  error: "bg-danger/20 text-danger",
  success: "bg-ok/20 text-ok",
};

const BADGE_BASE = "rounded-full px-1.5 text-[11px] font-medium";

/** Write a head tag's badge and bubble into its DOM nodes (no React render). */
export function applyTag(els: { badge: HTMLElement | null; bubble: HTMLElement | null }, visual: VisualState | undefined): void {
  if (els.badge) {
    els.badge.textContent = visual?.badge.text ?? "";
    els.badge.className = `${BADGE_BASE} ${visual ? TONE_CLASS[visual.badge.tone] : "hidden"}`;
  }
  if (els.bubble) {
    // a bubble that only repeats the badge ("等待審批…" under "等待審批") says nothing new
    const bubble = visual?.bubble && visual.bubble.replace(/…$/, "") !== visual.badge.text ? visual.bubble : "";
    els.bubble.textContent = bubble;
    els.bubble.hidden = !bubble;
  }
}

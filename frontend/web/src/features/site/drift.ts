// The market strip's slow drift (D-048): it moves by itself, slowly enough to read, and loops.
// Written here rather than taken from a widget so its speed and manners are ours: it stops under
// the pointer or the keyboard, gives way to a hand on a phone for a moment, and does not move at
// all for a reader who asked the system for less motion, or when everything already fits.
"use client";

import { useEffect, useState, type RefObject } from "react";

/** Pixels a second: slow enough to read a figure as it passes. */
export const DRIFT_SPEED = 30;
/** After a touch or an arrow, how long the strip waits before moving again (ms). */
export const RESUME_AFTER = 4000;

/** Where the strip is after ``seconds`` more, wrapped at ``period`` (the width of one copy of
 * the figures): the second copy then stands exactly where the first began, so the loop is seamless. */
export function advance(position: number, seconds: number, period: number, speed = DRIFT_SPEED): number {
  if (period <= 0) return position;
  const next = position + seconds * speed;
  return next >= period ? next - period : next;
}

/**
 * Drift ``list`` (a scrolling row holding the figures twice) to the left. Returns whether it
 * loops, i.e. whether the second copy should be drawn at all: not when one copy fits the row.
 */
export function useDrift(list: RefObject<HTMLElement | null>, copyLength: number): boolean {
  const [loops, setLoops] = useState(false);

  // does one copy overflow the row? (measured with only one copy drawn; again on resize)
  useEffect(() => {
    const row = list.current;
    if (!row || copyLength === 0) return;
    const measure = () => {
      const items = Array.from(row.children) as HTMLElement[];
      const first = items.slice(0, copyLength);
      const width = first.length ? first[first.length - 1].offsetLeft + first[first.length - 1].offsetWidth - first[0].offsetLeft : 0;
      setLoops(width > row.clientWidth);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(row);
    return () => observer?.disconnect();
  }, [list, copyLength]);

  useEffect(() => {
    const row = list.current;
    if (!row || !loops) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

    let position = row.scrollLeft;
    let last: number | null = null;
    let held = false; // the pointer or the keyboard is on it
    let restAt = 0; // until when a touch or an arrow keeps it still
    let frame = 0;

    const period = () => {
      const second = row.children[copyLength] as HTMLElement | undefined;
      const first = row.children[0] as HTMLElement | undefined;
      return second && first ? second.offsetLeft - first.offsetLeft : 0;
    };
    const tick = (now: number) => {
      const seconds = last === null ? 0 : Math.min((now - last) / 1000, 0.1);
      last = now;
      if (held || now < restAt || document.hidden) {
        position = row.scrollLeft; // pick up wherever a hand or an arrow left it
      } else {
        position = advance(position, seconds, period());
        row.scrollLeft = position;
      }
      frame = requestAnimationFrame(tick);
    };
    const hold = () => (held = true);
    const release = () => (held = false);
    const rest = () => (restAt = performance.now() + RESUME_AFTER);

    row.addEventListener("mouseenter", hold);
    row.addEventListener("mouseleave", release);
    row.addEventListener("focusin", hold);
    row.addEventListener("focusout", release);
    row.addEventListener("touchstart", rest, { passive: true });
    row.addEventListener("wheel", rest, { passive: true });
    row.addEventListener("drift:rest", rest);
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      row.removeEventListener("mouseenter", hold);
      row.removeEventListener("mouseleave", release);
      row.removeEventListener("focusin", hold);
      row.removeEventListener("focusout", release);
      row.removeEventListener("touchstart", rest);
      row.removeEventListener("wheel", rest);
      row.removeEventListener("drift:rest", rest);
    };
  }, [list, loops, copyLength]);

  return loops;
}

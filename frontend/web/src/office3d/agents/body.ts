// How big a figure is and where it sits (T-405). Kept apart from ``AgentAvatar`` so that what
// draws a figure without React — the 2D bake (D-027) — reads the same numbers as the 3D office.

/** Kenney's characters are 0.67 units tall; 2 makes a 1.35 m chibi whose head clears the chair back. */
export const AVATAR_SCALE = 2;

/** Seated, the body is lifted so the hips rest on the chair (seat top 0.46 m). */
export const SEAT_LIFT = 0.41;

/** Standing up (done), the avatar steps behind its chair. */
export const STAND_BACK = 0.6;

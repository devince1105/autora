// The sprites still drawn by hand (T-410 stage 4): the people, and what they carry.
//
// Everything else on the 2D floor — the room, the furniture, the walls — is baked from the 3D
// office now (D-026, D-027: ``tools/bake-sprites``, ``art/baked.ts``). The people are next: until
// they are baked from the 3D characters too, they are this 12×18 sprite drawn at twice its size.

import { sprite, type Palette } from "./sprites";

/** The tones the people are drawn in. ``S`` (shirt) and ``H`` (hair) are repainted per agent. */
const PAL: Palette = {
  // skin and hair
  p: "#c78a5e",
  q: "#f0d2b4",
  r: "#2a1d16",
  // trousers and shoes
  e: "#232c2e",
  // shirt, until an agent's colour replaces it
  n: "#35606d",
  // paper
  v: "#d9e2d5",
  w: "#f2f5ef",
  x: "#9aa89b",
  // the shadow at their feet, and its darkest edge
  y: "#122a25",
  z: "#0a1714",
};

/**
 * Somebody at work, 12×18: hair, face, arms, shirt, legs and a contact shadow.
 *
 * ``S`` is the shirt and ``H`` the hair — recoloured per agent, so the floor keeps the same
 * colour coding as the rest of the office. The second frame swaps the legs for walking.
 */
const PERSON_ROWS = (step: 0 | 1) => [
  "....HHHH....",
  "...HHHHHH...",
  "...HqqqqH...",
  "...qqqqqq...",
  "...qrqqrq...",
  "...qqqqqq...",
  "....qqqq....",
  "...SSSSSS...",
  "..pSSSSSSp..",
  "..pSSSSSSp..",
  "..pSSSSSSp..",
  "...SSSSSS...",
  "...SSSSSS...",
  "....ee.ee...",
  step === 0 ? "....ee.ee..." : "....e...e...",
  step === 0 ? "....ee.ee..." : "...ee...ee..",
  "...zz...zz..",
  "..yyyyyyyy..",
];

export const PERSON = [
  sprite(PERSON_ROWS(0), { ...PAL, S: PAL.n, H: PAL.r }, 17),
  sprite(PERSON_ROWS(1), { ...PAL, S: PAL.n, H: PAL.r }, 17),
];

/** What somebody carries between desks. */
export const DOCUMENT = sprite(["wwww", "wvvw", "wwww", "wvvw", "xxxx"], PAL, 4);

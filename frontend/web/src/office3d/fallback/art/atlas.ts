// The office's sprites (T-410 stage 4). Each one is drawn pixel by pixel, with its own
// silhouette and its own tones: a top surface, a side in shadow, a highlight where the light
// catches it, and a contact shadow on the floor. That is what makes the floor read as a game
// scene rather than a diagram of one.
//
// The palette is deliberately small — five or so tones per material — the way a pixel artist
// works from a ramp. Letters are shared across sprites so the whole floor stays one palette.

import { sprite, type Palette } from "./sprites";

/**
 * The floor's ramps. Each material runs darkest → lightest; shading is choosing a step on the
 * ramp, never blending between them.
 */
export const PAL: Palette = {
  // wood: desks, shelves, floors of the walled rooms
  a: "#4a3524",
  b: "#6b4e34",
  c: "#8a6743",
  d: "#a8824f",
  // metal and plastic: monitor stands, appliances, chair frames
  e: "#232c2e",
  f: "#3b4a4d",
  g: "#5d7175",
  h: "#8fa3a6",
  // greenery
  i: "#1d4a2c",
  j: "#2f7a43",
  k: "#46a75a",
  l: "#7fd07a",
  // upholstery (sofas, chairs): a blue-green the console palette already lives in
  m: "#26454f",
  n: "#35606d",
  o: "#4b8496",
  // skin and hair
  p: "#c78a5e",
  q: "#f0d2b4",
  r: "#2a1d16",
  // screens
  s: "#10201e",
  t: "#7fe3c0",
  u: "#3d8f78",
  // paper, ceramics, white goods
  v: "#d9e2d5",
  w: "#f2f5ef",
  x: "#9aa89b",
  // shadow on the floor, and the darkest outline
  y: "#122a25",
  z: "#0a1714",
};

/** A desk with a monitor, keyboard and mug: the unit the open plan is made of. */
export const DESK = sprite(
  [
    "................",
    "....eeeeeeee....",
    "...essssssse....",
    "...esttttuse....",
    "...esttttuse....",
    "...essssssse....",
    "....eeffffee....",
    "......ffff......",
    "cccccccccccccccc",
    "dddddddddddddddd",
    "cccvvvvccccxwwcc",
    "cccvvvvccccxwwcc",
    "bbbbbbbbbbbbbbbb",
    "aabbbbbbbbbbbbaa",
    "aa..........aa..",
    "yy..........yy..",
  ],
  PAL,
  14,
);

/** An office chair from above: back, seat, five-star base. */
export const CHAIR = sprite(
  [
    "................",
    ".....mmmmmm.....",
    "....mnnnnnnm....",
    "....mnooonnm....",
    "....mnnnnnnm....",
    "...mmmmmmmmmm...",
    "...mnnnnnnnnm...",
    "...mnooooonnm...",
    "...mmmmmmmmmm...",
    "......ffff......",
    "....fffeeeff....",
    "......ffff......",
    ".....yyyyyy.....",
    "................",
  ],
  PAL,
  12,
);

/** A two-seat sofa for the lounge: back, arms, cushions, a shadow along its foot. */
export const SOFA = sprite(
  [
    "..mmmmmmmmmmmmmmmmmmmmmmmmmmmm..",
    ".mnnnnnnnnnnnnnnnnnnnnnnnnnnnnm.",
    ".mnooooooooooooooooooooooooooonm",
    ".mnooooooooooooooooooooooooooonm",
    "mmnnnnnnnnnnnnnnnnnnnnnnnnnnnnmm",
    "mnnooooooooooonmmnooooooooooonnm",
    "mnnooooooooooonmmnooooooooooonnm",
    "mnnooooooooooonmmnooooooooooonnm",
    "mnnnnnnnnnnnnnmmnnnnnnnnnnnnnnnm",
    "mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmm",
    ".ee..........................ee.",
    ".yy..........................yy.",
  ],
  PAL,
  11,
);

/** A potted plant: pot, soil, stems and leaves in three greens. */
export const PLANT = sprite(
  [
    "......kk......",
    ".....kkll.....",
    "...jjkklkk....",
    "..jjkkkkllk...",
    ".ijjkkkkkkll..",
    ".ijjjkkkkkkl..",
    "..iijjkkkkl...",
    "...iijjkkl....",
    "....iijjk.....",
    ".....iij......",
    ".....bbb......",
    "....bcccb.....",
    "....bcccb.....",
    "....bbbbb.....",
    ".....yyy......",
  ],
  PAL,
  14,
);

/** A tall plant for corners: the same greens, more of them. */
export const PALM = sprite(
  [
    "...l....l...",
    "..lkl..lkl..",
    ".lkkjlljkkl.",
    "lkkjjkkjjkkl",
    ".jjkkkkkkjj.",
    "..jjkkkkjj..",
    "....jkkj....",
    "....ijji....",
    ".....ii.....",
    ".....ii.....",
    "....bbbb....",
    "...bccccb...",
    "...bccccb...",
    "...bbbbbb...",
    "....yyyy....",
  ],
  PAL,
  14,
);

/** A bookshelf: frame, shelves, and books in a few colours. */
export const SHELF = sprite(
  [
    "aaaaaaaaaaaaaaaa",
    "abbbbbbbbbbbbbba",
    "abmnobmnjkobmnba",
    "abmnobmnjkobmnba",
    "abbbbbbbbbbbbbba",
    "abjkonmbjkobmnba",
    "abjkonmbjkobmnba",
    "abbbbbbbbbbbbbba",
    "aaaaaaaaaaaaaaaa",
    "yyyyyyyyyyyyyyyy",
  ],
  PAL,
  9,
);

/** A low cabinet with two doors and a plant-sized top. */
export const CABINET = sprite(
  [
    "cccccccccccccccc",
    "dddddddddddddddd",
    "cbbbbbbccbbbbbbc",
    "cbbbbbbccbbbbbbc",
    "cbbhbbbccbbbhbbc",
    "cbbbbbbccbbbbbbc",
    "cbbbbbbccbbbbbbc",
    "aaaaaaaaaaaaaaaa",
    "yyyyyyyyyyyyyyyy",
  ],
  PAL,
  8,
);

/** The reception counter, tile-able: top surface, front panel, a lit edge. */
export const COUNTER = sprite(
  [
    "cccccccccccccccc",
    "dddddddddddddddd",
    "ccccccccccccccc c".replace(" ", "c"),
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "abbbbbbbbbbbbbba",
    "aaaaaaaaaaaaaaaa",
    "yyyyyyyyyyyyyyyy",
  ],
  PAL,
  7,
);

/** A coffee machine for the counter: body, group head, cup, steam wand. */
export const COFFEE = sprite(
  [
    "..eeeeeeee..",
    ".efffffffge.",
    ".efhhhhhfge.",
    ".efhssshfge.",
    ".efhssshfge.",
    ".efffffffge.",
    ".eftttttfge.",
    ".effffffffe.",
    "..ee.ww.ee..",
    "...e.ww.e...",
    "..eeeeeeee..",
    "...yyyyyy...",
  ],
  PAL,
  11,
);

/** A fridge: door, handle, a highlight down its left edge. */
export const FRIDGE = sprite(
  [
    "wwwwwwwwwwww",
    "wvvvvvvvvvvx",
    "wvvvvvvvvhvx",
    "wvvvvvvvvhvx",
    "wvvvvvvvvvvx",
    "wxxxxxxxxxxx",
    "wvvvvvvvvvvx",
    "wvvvvvvvvhvx",
    "wvvvvvvvvvvx",
    "wvvvvvvvvvvx",
    "xxxxxxxxxxxx",
    "yyyyyyyyyyyy",
  ],
  PAL,
  11,
);

/** A stove with four rings and an oven door. */
export const STOVE = sprite(
  [
    "eeeeeeeeeeee",
    "efffffffffge",
    "efgghfgghfge",
    "efgghfgghfge",
    "efffffffffge",
    "efgghfgghfge",
    "efgghfgghfge",
    "efffffffffge",
    "ehhhhhhhhhhe",
    "efsssssssffe",
    "eeeeeeeeeeee",
    "yyyyyyyyyyyy",
  ],
  PAL,
  11,
);

/** A whiteboard on a meeting-room wall, with something written on it. */
export const WHITEBOARD = sprite(
  [
    "xxxxxxxxxxxxxxxx",
    "xwwwwwwwwwwwwwwx",
    "xwwttwwwwuuuwwwx",
    "xwwwwwwwwwwwwwwx",
    "xwwuuuwwwttwwwwx",
    "xwwwwwwwwwwwwwwx",
    "xxxxxxxxxxxxxxxx",
  ],
  PAL,
  6,
);

/** A standing lamp: shade, stem, base, and the light it throws on the floor. */
export const LAMP = sprite(
  [
    "..wwww..",
    ".wttttw.",
    ".wttttw.",
    "..hhhh..",
    "...ff...",
    "...ff...",
    "...ff...",
    "...ff...",
    "..ffff..",
    ".eeeeee.",
    "..yyyy..",
  ],
  PAL,
  10,
);

/** A window in the top wall: frame, glass, and the light on the sill. */
export const WINDOW = sprite(
  [
    "xxxxxxxxxxxxxxxx",
    "xhhhhhhxhhhhhhhx",
    "xhttttthxttttthx",
    "xhttttthxttttthx",
    "xhhhhhhxhhhhhhhx",
    "xxxxxxxxxxxxxxxx",
    "wwwwwwwwwwwwwwww",
  ],
  PAL,
  6,
);

/** The door: a frame and a leaf, standing open. */
export const DOOR = sprite(
  [
    "aaaaaa",
    "abbbba",
    "abccba",
    "abccba",
    "abccba",
    "abcchb",
    "abccba",
    "abccba",
    "abbbba",
    "aaaaaa",
  ],
  PAL,
  9,
);

/** Small things that fill a surface: papers, a mug, a keyboard, a plate. */
export const PAPERS = sprite(["..wwww..", ".wwwwww.", ".wvvvvw.", ".wwwwww.", "..xxxx.."], PAL, 4);
export const MUG = sprite(["..ww..", ".wttw.", ".wttwx", ".wwwx.", "..xx.."], PAL, 4);
export const PLATE = sprite([".wwww.", "wvvvvw", "wvtvvw", ".wwww."], PAL, 3);
export const BOX = sprite(["cccccc", "cddddc", "cdbbdc", "cddddc", "cccccc", "yyyyyy"], PAL, 5);

/** A rug: a border and a pattern, four tones so it reads as fabric and not a flat block. */
export const RUG = sprite(
  [
    "nnnnnnnnnnnnnnnn",
    "nmmmmmmmmmmmmmmn",
    "nmoooooooooooomn",
    "nmoonnnnnnnnoomn",
    "nmoonoooooonoomn",
    "nmoonoooooonoomn",
    "nmoonnnnnnnnoomn",
    "nmoooooooooooomn",
    "nmmmmmmmmmmmmmmn",
    "nnnnnnnnnnnnnnnn",
  ],
  PAL,
  0,
);

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

export const PERSON = [sprite(PERSON_ROWS(0), { ...PAL, S: PAL.n, H: PAL.r }, 17), sprite(PERSON_ROWS(1), { ...PAL, S: PAL.n, H: PAL.r }, 17)];

/** What somebody carries between desks. */
export const DOCUMENT = sprite(["wwww", "wvvw", "wwww", "wvvw", "xxxx"], PAL, 4);

// --- tiles ------------------------------------------------------------------------------------
//
// The ground is sprites too: a 16×16 tile with a seam, a couple of speckles and a lighter
// corner where the light falls. Three variants, so a wide floor does not repeat visibly.

const FLOOR_PAL: Palette = { "1": "#1b3a31", "2": "#173229", "3": "#22483c", "4": "#132b25" };

const floorRows = (variant: 0 | 1 | 2) => {
  const base = [
    "1111111111111111",
    "1333333333333331",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1311111111111131",
    "1222222222222221",
    "4444444444444444",
  ];
  if (variant === 1) {
    base[4] = "1311141111111131";
    base[9] = "1311111111411131";
  }
  if (variant === 2) {
    base[6] = "1311111144111131";
    base[11] = "1341111111111131";
  }
  return base;
};

export const FLOOR = [0, 1, 2].map((variant) => sprite(floorRows(variant as 0 | 1 | 2), FLOOR_PAL, 0));

/** A carpeted tile: the zone's colour is ``C``, recoloured per room. */
export const CARPET = sprite(
  [
    "CCCCCCCCCCCCCCCC",
    "CDDDDDDDDDDDDDDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCECCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCECCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDCCCCCCCCCCCCDC",
    "CDDDDDDDDDDDDDDC",
    "CCCCCCCCCCCCCCCC",
  ],
  { C: "#1f473c", D: "#26564a", E: "#2c6153" },
  0,
);

/** The corridor: a lighter walkway with a scuffed centre line. */
export const WALKWAY = sprite(
  [
    "GGGGGGGGGGGGGGGG",
    "GHHHHHHHHHHHHHHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGIIIIGGGGHG",
    "GHGGGGIIIIGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHGGGGGGGGGGGGHG",
    "GHHHHHHHHHHHHHHG",
    "GGGGGGGGGGGGGGGG",
  ],
  { G: "#2c6050", H: "#34705e", I: "#255346" },
  0,
);

/** A wall seen from above: its top face. The face below it is drawn by ``WALL_FACE``. */
export const WALL_TOP = sprite(
  [
    "JJJJJJJJJJJJJJJJ",
    "KKKKKKKKKKKKKKKK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KLLLLLLLLLLLLLLK",
    "KKKKKKKKKKKKKKKK",
    "JJJJJJJJJJJJJJJJ",
  ],
  { J: "#0b1a16", K: "#1c463a", L: "#245546" },
  0,
);

/** The face of a wall where it meets the floor: what gives the room its height. */
export const WALL_FACE = sprite(
  [
    "MMMMMMMMMMMMMMMM",
    "NNNNNNNNNNNNNNNN",
    "NNNNNNNNNNNNNNNN",
    "OOOOOOOOOOOOOOOO",
    "PPPPPPPPPPPPPPPP",
  ],
  { M: "#2e6a58", N: "#1e4a3e", O: "#143229", P: "#0e241d" },
  0,
);

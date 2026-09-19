// Colours of the office (D-008 characters, D-010 scene, T-413 styles, D-011). One palette per
// interior-design style; the scene is built from the chosen one (a switch rebuilds the merged
// furniture mesh, floors and labels). Plain colours; floors may use textures drawn in code
// (scene/textures.ts).
//
//   muji       — 日式無印, the default: pale beech, white walls, linen and soft greys, calm;
//   wabisabi   — 侘寂: warm plaster, weathered wood, clay and moss, low light and low contrast;
//   industrial — 工業風: concrete, black steel and glazing, reclaimed wood, cognac leather, brick red;
//   google     — Google 風 (a software company's office): white, light wood, bold primary colours
//                per zone, playful furniture (colours only: no logos or brand assets);
//   cyber      — 電光風 (cyberpunk), the night look: dark floors with a glowing grid, dim blue
//                light, electric neon across the spectrum, lit desk edges.

export type ThemeId = "muji" | "wabisabi" | "industrial" | "google" | "cyber";

export type FloorKind =
  | "base"
  | "corridor"
  | "research"
  | "editorial"
  | "growth"
  | "spare"
  | "lobby"
  | "ceo"
  | "meeting"
  | "pantry"
  | "rugLounge"
  | "rugCeo"
  | "entranceMat";

export interface FloorLook {
  /** The floor's colour (with a texture: the colour the texture is painted in). */
  color: string;
  roughness: number;
  texture?: "wood" | "tile" | "stone" | "carpet" | "grid";
  /** A grid's line colour. */
  lines?: string;
  /** Metres one texture tile covers. */
  metres?: number;
  /** A faint light of its own (neon under a rug, a lit walkway). */
  emissive?: string;
  /** The texture glows too (a grid's lines), this strongly. */
  glowMap?: number;
}

export interface Lighting {
  /** Hemisphere light: sky and ground colours, intensity. */
  sky: string;
  ground: string;
  hemisphere: number;
  /** The key light from the upper left (it casts the shadows). */
  key: string;
  keyIntensity: number;
  /** How strongly the baked light panels light the scene (reflections and fill). */
  environment: number;
  /** The windows' own glow (the world outside). */
  windowGlow: string;
  windowGlowIntensity: number;
}

export interface Palette {
  /** The page behind the diorama, top to bottom. */
  backdrop: [string, string];
  lighting: Lighting;
  /**
   * Colours drawn unlit, so they glow (neon): any part painted in one of them. Room signs and
   * floor lettering glow too when this is not empty.
   */
  glow: string[];
  slab: string;
  wall: string;
  wallCap: string;
  floors: Record<FloorKind, FloorLook>;

  deskTop: string;
  metal: string;
  chair: string;
  monitor: string;
  screenOff: string;
  keyboard: string;
  pedestal: string;

  shelfWood: string;
  shelfDark: string;
  books: string[];
  pot: string;
  potTerracotta: string;
  soil: string;
  leaf: string;
  leafLight: string;
  trunk: string;

  sofa: string;
  cushion: string;
  armchair: string;
  tableWood: string;
  execWood: string;
  glassTable: string;

  /** Door frames and open leaves in the glass fronts, and the entrance. */
  door: string;
  /** Posts and rails of the glass fronts. */
  mullion: string;
  glass: string;
  /** Window frames, skirting. */
  frame: string;
  windowGlass: string;
  picture: string[];

  counter: string;
  counterFront: string;
  counterTop: string;
  fridge: string;
  vending: string;
  vendingGlass: string;
  cooler: string;
  coolerBottle: string;
  whiteboard: string;
  approvalAccent: string;
  /** Neon trim: a glowing outline around each zone's floor, and along the desks' front edges. */
  zoneTrim?: Partial<Record<"research" | "editorial" | "growth" | "spare" | "lobby" | "pantry", string>>;
  deskEdge?: string;
  /** Room signs: plate and lettering. */
  sign: string;
  signText: string;
  /** Lettering painted on the floor (department names). */
  floorText: string;
}

const MUJI: Palette = {
  backdrop: ["#f3f1ec", "#d4cdc2"],
  lighting: {
    sky: "#ffffff",
    ground: "#d6cdbf",
    hemisphere: 0.75,
    key: "#fffaf2",
    keyIntensity: 3.0,
    environment: 1,
    windowGlow: "#f1f6fa",
    windowGlowIntensity: 0.55,
  },
  glow: [],
  slab: "#f5f3ef",
  wall: "#f6f3ee",
  wallCap: "#ffffff",
  floors: {
    base: { color: "#e3cfad", roughness: 0.6, texture: "wood", metres: 1.2 },
    corridor: { color: "#e6e2da", roughness: 0.45 },
    research: { color: "#cdd4d9", roughness: 0.95, texture: "carpet", metres: 1 },
    editorial: { color: "#d2d8ca", roughness: 0.95, texture: "carpet", metres: 1 },
    growth: { color: "#e2d5c4", roughness: 0.95, texture: "carpet", metres: 1 },
    spare: { color: "#dcd8d0", roughness: 0.95, texture: "carpet", metres: 1 },
    lobby: { color: "#ece7de", roughness: 0.45, texture: "stone", metres: 1.2 },
    ceo: { color: "#c9a980", roughness: 0.55, texture: "wood", metres: 1 },
    meeting: { color: "#d5d4cf", roughness: 0.95, texture: "carpet", metres: 1 },
    pantry: { color: "#f4f2ed", roughness: 0.35, texture: "tile", metres: 0.5 },
    rugLounge: { color: "#d9cdb9", roughness: 0.95 },
    rugCeo: { color: "#cfc3b1", roughness: 0.95 },
    entranceMat: { color: "#8c8378", roughness: 1 },
  },
  deskTop: "#ead9bc",
  metal: "#8e8c88",
  chair: "#9d988f",
  monitor: "#2a2b2f",
  screenOff: "#1c1d21",
  keyboard: "#d9d6d0",
  pedestal: "#e4e0d9",
  shelfWood: "#dcc4a0",
  shelfDark: "#b89d78",
  books: ["#c9bba5", "#8c9aa6", "#e8e1d4", "#a8b39b", "#b8a48b", "#f2eee6", "#9a8f84"],
  pot: "#f1ede6",
  potTerracotta: "#c49a7a",
  soil: "#6b5a48",
  leaf: "#5f8a52",
  leafLight: "#7ea56a",
  trunk: "#8a7a5f",
  sofa: "#e8e0d2",
  cushion: "#d3c8b6",
  armchair: "#b9ad9d",
  tableWood: "#d2b58c",
  execWood: "#b8966e",
  glassTable: "#e6eef0",
  door: "#cdb28a",
  mullion: "#bcb5aa",
  glass: "#e2eef2",
  frame: "#ffffff",
  windowGlass: "#e9f3f8",
  picture: ["#e6dccd", "#cfd6cc", "#ece2c8", "#d8d4cc"],
  counter: "#f3f0ea",
  counterFront: "#dcc4a0",
  counterTop: "#d9d6d0",
  fridge: "#e2e0dc",
  vending: "#cfcac1",
  vendingGlass: "#3a3d44",
  cooler: "#f4f4f2",
  coolerBottle: "#bcd8e6",
  whiteboard: "#fbfbfa",
  approvalAccent: "#8b7a64",
  sign: "#6e645a",
  signText: "#ffffff",
  floorText: "#6b6258",
};

const WABISABI: Palette = {
  ...MUJI,
  backdrop: ["#eee7dc", "#c4b5a0"],
  lighting: {
    sky: "#fff6ea",
    ground: "#b9a88f",
    hemisphere: 0.75,
    key: "#ffeccf",
    keyIntensity: 2.6,
    environment: 0.8,
    windowGlow: "#fff1dc",
    windowGlowIntensity: 0.5,
  },
  slab: "#e8e0d4",
  wall: "#e4dacb",
  wallCap: "#eee6da",
  floors: {
    base: { color: "#bea888", roughness: 0.7, texture: "wood", metres: 1.4 },
    corridor: { color: "#d3c8b6", roughness: 0.6, texture: "stone", metres: 1.4 },
    research: { color: "#c4bbad", roughness: 0.95, texture: "carpet", metres: 1 },
    editorial: { color: "#b9bca6", roughness: 0.95, texture: "carpet", metres: 1 },
    growth: { color: "#cdb39a", roughness: 0.95, texture: "carpet", metres: 1 },
    spare: { color: "#cfc6b8", roughness: 0.95, texture: "carpet", metres: 1 },
    lobby: { color: "#d8cdbb", roughness: 0.6, texture: "stone", metres: 1.4 },
    ceo: { color: "#8c6f55", roughness: 0.65, texture: "wood", metres: 1 },
    meeting: { color: "#c6bdb0", roughness: 0.95, texture: "carpet", metres: 1 },
    pantry: { color: "#e0d7c9", roughness: 0.5, texture: "tile", metres: 0.5 },
    rugLounge: { color: "#b5a288", roughness: 0.95 },
    rugCeo: { color: "#a69580", roughness: 0.95 },
    entranceMat: { color: "#6e6254", roughness: 1 },
  },
  deskTop: "#c7ad88",
  metal: "#4f463e",
  chair: "#7d6e5e",
  keyboard: "#8a7f72",
  pedestal: "#b3a591",
  shelfWood: "#a68a69",
  shelfDark: "#6b5a4a",
  books: ["#a8927a", "#7f7a68", "#c9b99f", "#8e7f6a", "#b5a58c", "#d9ceba", "#6f6557"],
  pot: "#b3a58f",
  potTerracotta: "#9a6b50",
  soil: "#4f4236",
  leaf: "#6f7d54",
  leafLight: "#8a9668",
  trunk: "#6e5e48",
  sofa: "#d5c8b3",
  cushion: "#bba993",
  armchair: "#9a8570",
  tableWood: "#8c6f55",
  execWood: "#6b5444",
  glassTable: "#c9c2b5",
  door: "#6b5a4a",
  mullion: "#6b5a4a",
  glass: "#dfe6e2",
  frame: "#e9e1d4",
  windowGlass: "#eef1ea",
  picture: ["#c9b99f", "#a8a58f", "#d9c6a5", "#b8ab98"],
  counter: "#d9cebd",
  counterFront: "#a68a69",
  counterTop: "#6e6254",
  fridge: "#cfc6b8",
  vending: "#8a7a68",
  vendingGlass: "#3d3a34",
  cooler: "#e4dccf",
  coolerBottle: "#b8c9c4",
  whiteboard: "#f1ece3",
  approvalAccent: "#6b5a4a",
  sign: "#6b5a4a",
  signText: "#f2ece2",
  floorText: "#6b5a4a",
};

const INDUSTRIAL: Palette = {
  ...MUJI,
  backdrop: ["#d9d5cf", "#8b8680"],
  lighting: {
    sky: "#fff8ee",
    ground: "#7f766b",
    hemisphere: 0.65,
    key: "#fff0d8",
    keyIntensity: 3.0,
    environment: 0.85,
    windowGlow: "#f3efe6",
    windowGlowIntensity: 0.5,
  },
  slab: "#b9b5ae",
  wall: "#aba7a0",
  wallCap: "#2a2b2e",
  floors: {
    base: { color: "#a19d96", roughness: 0.45, texture: "stone", metres: 1.6 },
    corridor: { color: "#86827c", roughness: 0.35 },
    research: { color: "#5f6873", roughness: 0.95, texture: "carpet", metres: 1 },
    editorial: { color: "#6c6b5b", roughness: 0.95, texture: "carpet", metres: 1 },
    growth: { color: "#7b5b49", roughness: 0.95, texture: "carpet", metres: 1 },
    spare: { color: "#6f6d6a", roughness: 0.95, texture: "carpet", metres: 1 },
    lobby: { color: "#8f8a84", roughness: 0.4, texture: "stone", metres: 1.2 },
    ceo: { color: "#5e412d", roughness: 0.5, texture: "wood", metres: 1 },
    meeting: { color: "#666b70", roughness: 0.95, texture: "carpet", metres: 1 },
    pantry: { color: "#e2ded6", roughness: 0.3, texture: "tile", metres: 0.5 },
    rugLounge: { color: "#8a6a50", roughness: 0.95 },
    rugCeo: { color: "#6e5a48", roughness: 0.95 },
    entranceMat: { color: "#2e2d2b", roughness: 1 },
  },
  deskTop: "#8e6c4b",
  metal: "#1f2023",
  chair: "#2b2c2f",
  keyboard: "#3a3b3f",
  pedestal: "#4a4b4f",
  shelfWood: "#6f4d35",
  shelfDark: "#1f2023",
  books: ["#9a4a32", "#4e5f6e", "#c29a5a", "#5f6b4f", "#7a5a45", "#d9d2c4", "#3a3b3f"],
  pot: "#6e6b66",
  potTerracotta: "#a0583a",
  soil: "#3e342b",
  leaf: "#4f7a45",
  leafLight: "#6a9458",
  trunk: "#5e5040",
  sofa: "#8a4b2a",
  cushion: "#a8623a",
  armchair: "#5a3a28",
  tableWood: "#6f4d35",
  execWood: "#4a3325",
  glassTable: "#b8c4c6",
  door: "#1f2023",
  mullion: "#1f2023",
  glass: "#cfdde0",
  frame: "#1f2023",
  windowGlass: "#dde9ec",
  picture: ["#b0422e", "#c29a5a", "#4e5f6e", "#d9d2c4"],
  counter: "#3a3b3f",
  counterFront: "#2a2b2e",
  counterTop: "#8e6c4b",
  fridge: "#a3a5a8",
  vending: "#a8412e",
  vendingGlass: "#232a33",
  cooler: "#c9c9c6",
  coolerBottle: "#9cc0cf",
  whiteboard: "#f4f4f2",
  approvalAccent: "#b0422e",
  sign: "#1f2023",
  signText: "#f2b35a",
  floorText: "#2a2a2c",
};

const GOOGLE: Palette = {
  ...MUJI,
  backdrop: ["#f1f3f4", "#c6d4ea"],
  lighting: {
    sky: "#ffffff",
    ground: "#d9dde3",
    hemisphere: 0.8,
    key: "#ffffff",
    keyIntensity: 3.1,
    environment: 1,
    windowGlow: "#eef5ff",
    windowGlowIntensity: 0.55,
  },
  slab: "#f5f6f7",
  wall: "#f8f9fa",
  floors: {
    base: { color: "#e2cfb0", roughness: 0.55, texture: "wood", metres: 1.2 },
    corridor: { color: "#eceef1", roughness: 0.4 },
    research: { color: "#7fa9ee", roughness: 0.95, texture: "carpet", metres: 1 },
    editorial: { color: "#f5cf5c", roughness: 0.95, texture: "carpet", metres: 1 },
    growth: { color: "#71c68d", roughness: 0.95, texture: "carpet", metres: 1 },
    spare: { color: "#f08b7e", roughness: 0.95, texture: "carpet", metres: 1 },
    lobby: { color: "#f3f3f1", roughness: 0.4, texture: "stone", metres: 1.2 },
    ceo: { color: "#c89b6d", roughness: 0.55, texture: "wood", metres: 1 },
    meeting: { color: "#b9d3f5", roughness: 0.95, texture: "carpet", metres: 1 },
    pantry: { color: "#ffffff", roughness: 0.35, texture: "tile", metres: 0.5 },
    rugLounge: { color: "#f28b82", roughness: 0.95 },
    rugCeo: { color: "#aecbfa", roughness: 0.95 },
    entranceMat: { color: "#5f6368", roughness: 1 },
  },
  deskTop: "#ffffff",
  metal: "#5f6368",
  chair: "#3c4043",
  keyboard: "#dadce0",
  pedestal: "#e8eaed",
  shelfWood: "#dcc095",
  shelfDark: "#5f6368",
  books: ["#4285f4", "#ea4335", "#fbbc04", "#34a853", "#a142f4", "#f8f9fa", "#24c1e0"],
  pot: "#ffffff",
  potTerracotta: "#fbbc04",
  leaf: "#3f9a4a",
  leafLight: "#5cc15e",
  sofa: "#4285f4",
  cushion: "#fbbc04",
  armchair: "#ea4335",
  tableWood: "#dcc095",
  execWood: "#8a6a4f",
  glassTable: "#e8f0fe",
  door: "#4285f4",
  mullion: "#ffffff",
  glass: "#d2e3fc",
  frame: "#ffffff",
  windowGlass: "#e8f0fe",
  picture: ["#4285f4", "#ea4335", "#fbbc04", "#34a853"],
  counter: "#ffffff",
  counterFront: "#34a853",
  counterTop: "#3c4043",
  fridge: "#dadce0",
  vending: "#ea4335",
  vendingGlass: "#202124",
  cooler: "#ffffff",
  coolerBottle: "#8ab4f8",
  whiteboard: "#ffffff",
  approvalAccent: "#fbbc04",
  sign: "#4285f4",
  signText: "#ffffff",
  floorText: "#202124",
};

const NEON = {
  red: "#ff3355",
  orange: "#ff8a1f",
  yellow: "#ffe11f",
  green: "#39ff7a",
  cyan: "#19e6ff",
  blue: "#2f7bff",
  violet: "#a45cff",
  pink: "#ff2fd1",
} as const;

const CYBER: Palette = {
  backdrop: ["#0d1330", "#03040a"],
  lighting: {
    sky: "#6f8cff",
    ground: "#0c1024",
    hemisphere: 1.2,
    key: "#b9c8ff",
    keyIntensity: 2.2,
    environment: 0.35,
    windowGlow: "#2f7bff",
    windowGlowIntensity: 0.9,
  },
  glow: Object.values(NEON),
  slab: "#0f1119",
  wall: "#1a1d2a",
  wallCap: NEON.blue,
  floors: {
    base: { color: "#12141d", roughness: 0.3, texture: "grid", lines: "#1c3d8a", metres: 1, glowMap: 0.8 },
    corridor: { color: "#0d0f17", roughness: 0.2, texture: "grid", lines: "#136b8a", metres: 1, glowMap: 1 },
    research: { color: "#18223f", roughness: 0.9, texture: "carpet", metres: 1 },
    editorial: { color: "#221a3a", roughness: 0.9, texture: "carpet", metres: 1 },
    growth: { color: "#132a24", roughness: 0.9, texture: "carpet", metres: 1 },
    spare: { color: "#26241a", roughness: 0.9, texture: "carpet", metres: 1 },
    lobby: { color: "#1f1c22", roughness: 0.25, texture: "stone", metres: 1.2 },
    ceo: { color: "#2e2630", roughness: 0.4, texture: "wood", metres: 1 },
    meeting: { color: "#18203a", roughness: 0.9, texture: "carpet", metres: 1 },
    pantry: { color: "#22222a", roughness: 0.3, texture: "tile", metres: 0.5 },
    rugLounge: { color: "#2a0f3a", roughness: 0.9, emissive: "#2a0636" },
    rugCeo: { color: "#0f2640", roughness: 0.9, emissive: "#041a30" },
    entranceMat: { color: "#1a2a10", roughness: 1, emissive: "#1f5a0a" },
  },
  deskTop: "#232633",
  metal: "#0e1016",
  chair: "#1a1c25",
  monitor: "#0b0c12",
  screenOff: "#0a0b10",
  keyboard: "#191b23",
  pedestal: "#2b2e3a",
  shelfWood: "#262936",
  shelfDark: "#14151d",
  books: [NEON.red, NEON.orange, NEON.yellow, NEON.green, NEON.cyan, NEON.violet, "#2a2d3a", "#343848"],
  pot: "#1d1f28",
  potTerracotta: "#262431",
  soil: "#0d0e12",
  leaf: "#138a5a",
  leafLight: "#2fd98a",
  trunk: "#2e3040",
  sofa: "#232638",
  cushion: "#35305a",
  armchair: "#1b3050",
  tableWood: "#272a36",
  execWood: "#191a22",
  glassTable: "#1f5f7a",
  door: NEON.green,
  mullion: NEON.cyan,
  glass: "#2f7bff",
  frame: "#232633",
  windowGlass: "#10245a",
  picture: [NEON.pink, NEON.orange, NEON.violet, NEON.yellow],
  counter: "#1f212b",
  counterFront: "#2a2d3b",
  counterTop: "#0e1016",
  fridge: "#2a2d3c",
  vending: "#1e2030",
  vendingGlass: NEON.orange,
  cooler: "#262936",
  coolerBottle: NEON.cyan,
  whiteboard: "#1a1d2a",
  approvalAccent: NEON.yellow,
  zoneTrim: { research: NEON.cyan, editorial: NEON.violet, growth: NEON.green, spare: NEON.yellow, lobby: NEON.orange, pantry: NEON.red },
  deskEdge: NEON.cyan,
  sign: "#07080e",
  signText: NEON.green,
  floorText: NEON.cyan,
};

export const THEMES: Record<ThemeId, { label: string; palette: Palette }> = {
  muji: { label: "日式無印", palette: MUJI },
  wabisabi: { label: "侘寂風", palette: WABISABI },
  industrial: { label: "工業風", palette: INDUSTRIAL },
  google: { label: "Google 風", palette: GOOGLE },
  cyber: { label: "電光風", palette: CYBER },
};

export const THEME_IDS = Object.keys(THEMES) as ThemeId[];
export const DEFAULT_THEME: ThemeId = "muji";

export function isThemeId(value: unknown): value is ThemeId {
  return typeof value === "string" && Object.hasOwn(THEMES, value);
}

/** Chair accents and (T-405) outfit colours per role: the same in every theme (they identify roles). */
export const ROLE_COLOR: Record<string, string> = {
  researcher: "#e0533d",
  analyst: "#f2c14e",
  writer: "#3f7fd6",
  editor: "#8e5bd6",
  marketing: "#3fb58a",
  ceo: "#2d2f33",
  spare: "#9aa0a8",
};

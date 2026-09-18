// Colours of the office (D-008 characters, D-010 scene): a bright isometric office — white
// walls with white caps, yellow corridors, a dark glossy work floor, white desks with black
// frames, dark chairs with coloured accents, warm wood, lots of plants. Plain colours; the only
// textures are generated in code (scene/textures.ts).
export const PALETTE = {
  slab: "#f3f4f6",
  wall: "#f1ede8",
  wallCap: "#ffffff",
  floorBase: "#d9dde2",
  floorWork: "#383b41",
  corridor: "#f2c14e",
  woodFloor: "#c99a66",
  meetingFloor: "#d9dde3",
  tileFloor: "#f2f2f0",
  rugLounge: "#ec9f95",
  rugCeo: "#b9cfe3",

  deskTop: "#f5f5f3",
  metal: "#2b2c30",
  chair: "#2f3136",
  monitor: "#25272c",
  screenOff: "#1a1c21",
  keyboard: "#3a3c42",
  pedestal: "#9ea3aa",

  shelfWood: "#d9a441",
  shelfDark: "#2d2f33",
  books: ["#d9534f", "#4f81bd", "#f0ad4e", "#5cb85c", "#8e6fbf", "#e8e8e8", "#3fb5b0"],
  pot: "#f4f4f4",
  potTerracotta: "#d8784f",
  soil: "#5b4636",
  leaf: "#3f9a4a",
  leafLight: "#5cc15e",
  trunk: "#8a7652",

  sofa: "#f1f1f1",
  cushion: "#d7dbe0",
  armchair: "#6b6f76",
  tableWood: "#c89b6d",
  execWood: "#5b4032",
  glassTable: "#cfe6ef",

  door: "#f2c14e",
  glass: "#cde8f2",
  frame: "#ffffff",
  windowGlass: "#dff1fb",
  picture: ["#f0c9b4", "#b8d8d0", "#f6e2a6", "#c9c3ea"],

  counter: "#f4f4f2",
  counterFront: "#e98b3a",
  counterTop: "#3a3c42",
  fridge: "#c5cacf",
  vending: "#d8342c",
  vendingGlass: "#253447",
  cooler: "#f4f4f4",
  coolerBottle: "#8fcbef",
  whiteboard: "#fbfbfb",
  approvalAccent: "#f2c14e",
} as const;

/** Chair accents and (T-405) outfit colours per role. */
export const ROLE_COLOR: Record<string, string> = {
  researcher: "#e0533d",
  analyst: "#f2c14e",
  writer: "#3f7fd6",
  editor: "#8e5bd6",
  marketing: "#3fb58a",
  ceo: "#2d2f33",
  spare: "#9aa0a8",
};

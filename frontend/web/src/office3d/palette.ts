// Colours of the office (D-008): bright, soft pastels (Good Job!-like offices, Animal
// Crossing-like warmth). Plain material colours, no textures (04 §7).
export const PALETTE = {
  sky: "#f7efe2",
  skyLight: "#fff8ec",
  groundLight: "#d9c8ad",
  floor: "#efe2c9",
  wall: "#f9e6d3",
  wallTrim: "#e3c3a0",
  window: "#cfe8f5",
  windowFrame: "#ffffff",
  glass: "#bfe3f0",
  deskTop: "#fdfaf3",
  deskLeg: "#c8a27a",
  chair: "#8ec5b8",
  chairBack: "#79b3a6",
  monitor: "#3d4556",
  screenOff: "#1e2430",
  meetingTable: "#d9b38c",
  approvalDesk: "#f3a6b8",
  pot: "#e9967a",
  leaf: "#8cc084",
} as const;

/** Area rugs: each zone has its own soft colour, so the room reads at a glance. */
export const ZONE_COLOR = {
  ceo: "#fbeec0",
  research: "#cfe9dc",
  editorial: "#fbd8c7",
  growth: "#dcd3f3",
  spare: "#e6eef7",
  approval: "#f8d6e0",
} as const;

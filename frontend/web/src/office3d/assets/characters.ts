// The avatar assets (T-404, D-008): Kenney "Mini Characters" 1.0, CC0 (see LICENSES.md). Twelve
// chibi low-poly characters, each a GLB with the same skeleton and 32 animations, sharing one
// texture. Which character an agent wears, and which clip plays for each pose, is decided here.
import type { Pose } from "../visual/mapping";

export const CHARACTER_DIR = "/models/characters";

export const CHARACTERS = [
  "character-male-a",
  "character-female-a",
  "character-male-b",
  "character-female-b",
  "character-male-c",
  "character-female-c",
  "character-male-d",
  "character-female-d",
  "character-male-e",
  "character-female-e",
  "character-male-f",
  "character-female-f",
] as const;
export type Character = (typeof CHARACTERS)[number];

export const characterUrl = (character: Character) => `${CHARACTER_DIR}/${character}.glb`;

/**
 * Clips per pose (02 §7 poses). The pack has one sitting clip; thinking, typing and reading are
 * the same seat with a different upper-body accent that T-405 layers on. `once` plays one time
 * and settles back into `base`.
 */
export const POSE_CLIP: Record<Pose, { base: string; once?: string }> = {
  sit_idle: { base: "sit" },
  sit_think: { base: "sit" },
  sit_type: { base: "sit", once: "interact-right" },
  sit_read: { base: "sit" },
  stand: { base: "idle", once: "emote-yes" },
  walk: { base: "walk" },
  slump: { base: "sit", once: "emote-no" },
};

/** The courier carrying a result (T-408). */
export const CARRY_CLIP = "pick-up";

export const REQUIRED_CLIPS = [...new Set([...Object.values(POSE_CLIP).flatMap((c) => [c.base, c.once ?? c.base]), CARRY_CLIP])].sort();

/** A character per agent: `avatar_key` if it names one, else a stable pick from the agent id. */
export function characterFor(agentId: string, avatarKey?: string | null): Character {
  if (avatarKey && (CHARACTERS as readonly string[]).includes(avatarKey)) return avatarKey as Character;
  let hash = 0;
  for (const ch of agentId) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return CHARACTERS[hash % CHARACTERS.length];
}

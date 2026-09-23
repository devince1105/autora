// The baked pieces, ready to paint (D-026, D-027).
//
// ``baked.ts`` stores each sprite's rows run-length encoded, because most of the office is long
// stretches of the same floor — the backdrop alone is 810 × 522 pixels, and written out one
// character per pixel it would be most of a megabyte of source. This expands a piece the first
// time anybody asks for it, and keeps the result.
import { BACKDROP_KEY, ENCODED, PLACED, type EncodedPiece } from "./baked";

export { BACKDROP_KEY, PLACED };

export interface BakedPiece extends Omit<EncodedPiece, "rows" | "shadow"> {
  /** One character per pixel; ``.`` is transparent. */
  rows: string[];
  /** ``#`` where its shadow falls on the floor, ``.`` elsewhere. */
  shadow: string[];
}

/** ``4ab2.`` -> ``aaaab..``. The inverse of the bake's encoder; a run's count is its digits. */
export function expand(encoded: string): string {
  let out = "";
  let count = "";
  for (const ch of encoded) {
    if (ch >= "0" && ch <= "9") {
      count += ch;
      continue;
    }
    out += ch.repeat(count ? Number(count) : 1);
    count = "";
  }
  if (count) throw new Error(`a run with no character after it: ${encoded.slice(-12)}`);
  return out;
}

const expanded = new Map<string, BakedPiece>();

/** A piece by its key (``desk``, ``decor:6:lounge``, ``backdrop``), expanded. Null if unknown. */
export function piece(key: string): BakedPiece | null {
  const cached = expanded.get(key);
  if (cached) return cached;
  const encoded = ENCODED[key];
  if (!encoded) return null;
  const full = { ...encoded, rows: encoded.rows.map(expand), shadow: encoded.shadow.map(expand) };
  expanded.set(key, full);
  return full;
}

export const PIECE_KEYS: readonly string[] = Object.keys(ENCODED);

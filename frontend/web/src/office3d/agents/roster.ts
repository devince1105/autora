// Who is in the office and where they sit (T-405/T-406). React sees this only when the roster
// changes: the selector returns one string (id, role, name, character per agent).
import { useMemo } from "react";

import { useRealtime } from "@/stores/realtime";

import { characterFor, type Character } from "../assets/characters";
import { assignSeats, type Seat } from "../scene/layout";

export interface Member {
  id: string;
  role: string;
  name: string;
  character: Character;
}

export function rosterKey(agents: Record<string, { id: string; role: string; display_name: string; avatar_key: string }> | undefined): string {
  return Object.values(agents ?? {})
    .map((a) => [a.id, a.role, a.display_name, characterFor(a.id, a.avatar_key)].join("\t"))
    .sort()
    .join("\n");
}

function parseRoster(key: string): Member[] {
  if (!key) return [];
  return key.split("\n").map((line) => {
    const [id, role, name, character] = line.split("\t");
    return { id, role, name, character: character as Character };
  });
}

export interface Roster {
  members: Member[];
  seats: Map<string, Seat>;
  /** seat key -> agent id, for per-desk things (screens, lamps). */
  bySeat: Map<string, string>;
}

export function useRoster(): Roster {
  const key = useRealtime((s) => rosterKey(s.company?.agents));
  return useMemo(() => {
    const members = parseRoster(key);
    const { seats } = assignSeats(members);
    const bySeat = new Map([...seats].map(([id, seat]) => [seat.key, id]));
    return { members, seats, bySeat };
  }, [key]);
}

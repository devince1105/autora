// Picking and keys (T-409, 04 §5): click an avatar to select it (the UI store; the detail panel
// and the camera follow from there), click empty space or press Esc to clear, 1–9 to jump to
// somebody. Nothing here fetches: the panel loads details from the selection.
//
// The numbers follow the room you are in (T-600 batch 3): inside a department they are its
// people, in the order the floor seats them; on the whole floor they are everybody. Indexing a
// fixed list of roles would have meant the keys pointed somewhere else as soon as a company had
// departments of its own.
import type { ThreeEvent } from "@react-three/fiber";

import { realtimeStore } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { assignSeats } from "../scene/layout";

export function avatarHandlers(agentId: string) {
  return {
    onClick: (event: ThreeEvent<MouseEvent>) => {
      event.stopPropagation();
      uiStore.getState().selectAgent(agentId);
    },
    onPointerOver: (event: ThreeEvent<PointerEvent>) => {
      event.stopPropagation();
      document.body.style.cursor = "pointer";
    },
    onPointerOut: () => {
      document.body.style.cursor = "";
    },
  };
}

/** The n-th agent of the room you are in — or of the whole floor, when you are outside one. */
export function agentForKey(key: string): string | null {
  const index = Number(key) - 1;
  if (!Number.isInteger(index) || index < 0 || index > 8) return null;
  const entered = uiStore.getState().focusedDepartment;
  const all = Object.values(realtimeStore.getState().company?.agents ?? {});
  const here = entered
    ? all.filter((a) => (a.department_key ?? a.office_zone_key) === entered.key)
    : all;
  const { seats } = assignSeats(all);
  const ordered = [...here].sort((a, b) => {
    const left = seats.get(a.id);
    const right = seats.get(b.id);
    if (left && right) return left.desk[0] - right.desk[0] || a.id.localeCompare(b.id);
    if (left) return -1; // somebody with a desk comes before somebody without one
    if (right) return 1;
    return a.id.localeCompare(b.id);
  });
  return ordered[index]?.id ?? null;
}

/** Esc clears the selection; 1–9 select somebody in the room. Ignored while typing. */
export function onOfficeKey(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null;
  if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
  if (event.key === "Escape") {
    uiStore.getState().selectAgent(null);
    return;
  }
  const id = agentForKey(event.key);
  if (id) uiStore.getState().selectAgent(id);
}

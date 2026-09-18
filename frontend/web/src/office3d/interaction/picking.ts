// Picking and keys (T-409, 04 §5): click an avatar to select it (the UI store; the detail panel
// and the camera follow from there), click empty space or press Esc to clear, 1–6 to jump to a
// role (a convenience). Nothing here fetches: the panel loads details from the selection.
import type { ThreeEvent } from "@react-three/fiber";

import { realtimeStore } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { ROLES } from "../scene/layout";

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

/** The first agent (by id) of the n-th role of the floor plan, if the company has one. */
export function agentForKey(key: string): string | null {
  const index = Number(key) - 1;
  if (!Number.isInteger(index) || index < 0 || index >= ROLES.length) return null;
  const role = ROLES[index];
  const agents = Object.values(realtimeStore.getState().company?.agents ?? {})
    .filter((a) => a.role === role)
    .sort((a, b) => a.id.localeCompare(b.id));
  return agents[0]?.id ?? null;
}

/** Esc clears the selection; 1–6 select a role. Ignored while typing in a field. */
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

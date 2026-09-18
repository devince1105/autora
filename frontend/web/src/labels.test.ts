// The office (office3d) and the 2D screens (features) must name roles and states alike; they
// cannot import each other (eslint boundaries), so this test holds them together.
import { describe, expect, it } from "vitest";

import { ROLE_LABEL as PANEL_ROLES, STATE_LABEL } from "@/features/agent-panel/model";
import { ROLE_LABEL as OFFICE_ROLES, visualState } from "@/office3d/visual/mapping";
import { ACTIVITY_STATES } from "@/stores/realtime";

describe("the office and the panels use the same words", () => {
  it("roles", () => {
    for (const [role, label] of Object.entries(OFFICE_ROLES)) expect(PANEL_ROLES[role]).toBe(label);
  });

  it("states (waiting says why in both, so only the others are compared)", () => {
    for (const state of ACTIVITY_STATES.filter((s) => s !== "WAITING")) {
      expect(visualState({ state, detail: {}, role: "writer" }).badge.text).toBe(STATE_LABEL[state]);
    }
  });
});

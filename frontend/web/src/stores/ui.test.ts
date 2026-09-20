import { describe, expect, it } from "vitest";

import { createUiStore } from "./ui";

describe("ui store", () => {
  it("selecting an agent opens its panel on the live tab", () => {
    const ui = createUiStore();
    ui.getState().selectAgent("a1");
    ui.getState().setPanelTab("output");
    ui.getState().selectAgent("a2");
    expect(ui.getState()).toMatchObject({ selectedAgentId: "a2", panelTab: "live" });
    ui.getState().setPanelTab("steps");
    ui.getState().selectAgent("a2"); // same agent: keep the tab
    expect(ui.getState().panelTab).toBe("steps");
  });

  it("the camera follows only a selected agent", () => {
    const ui = createUiStore();
    ui.getState().setCameraMode("follow");
    expect(ui.getState().cameraMode).toBe("overview");
    ui.getState().selectAgent("a1");
    ui.getState().setCameraMode("follow");
    expect(ui.getState().cameraMode).toBe("follow");
    ui.getState().selectAgent(null);
    expect(ui.getState().cameraMode).toBe("overview");
    ui.getState().setCameraMode("free");
    expect(ui.getState().cameraMode).toBe("free");
  });

  it("timeline pause and filters, and reset", () => {
    const ui = createUiStore();
    ui.getState().setTimelinePaused(true);
    ui.getState().setFilters({ agentIds: ["a1"] });
    ui.getState().setFilters({ eventTypes: ["TOOL_CALLED"] });
    expect(ui.getState().filters).toEqual({ agentIds: ["a1"], eventTypes: ["TOOL_CALLED"] });
    ui.getState().reset();
    expect(ui.getState()).toMatchObject({
      selectedAgentId: null, panelTab: "live", cameraMode: "overview", timelinePaused: false,
      filters: { agentIds: [], eventTypes: [] },
    });
  });

  it("holds no domain data", () => {
    const keys = Object.keys(createUiStore().getState()).filter(
      (k) => typeof (createUiStore().getState() as unknown as Record<string, unknown>)[k] !== "function",
    );
    expect(keys.sort()).toEqual([
      "cameraMode",
      "filters",
      // which department the operator is looking inside: a view, not company data (T-600)
      "focusedDepartment",
      "panelTab",
      "selectedAgentId",
      "timelinePaused",
    ]);
  });
});

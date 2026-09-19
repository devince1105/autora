import { describe, expect, it } from "vitest";

import { describeEvent } from "./describe";

describe("describeEvent: newsroom sources and evidence (T-501, T-502)", () => {
  it("a poll reads as new items of total, or as a failure with its reason", () => {
    expect(describeEvent("SOURCE_POLLED", { count: 2, seen: 5, error: null })).toMatchObject({
      label: "讀取來源",
      tone: "neutral",
      summary: "新增 2 則・共 5 則",
      known: true,
    });
    expect(describeEvent("SOURCE_POLLED", { count: 0, seen: 0, error: "FetchRefused: 404" })).toMatchObject({
      label: "來源讀取失敗",
      tone: "warn",
      summary: "FetchRefused: 404",
    });
    expect(describeEvent("SOURCE_ITEM_DISCOVERED", { title: "微電網啟用" }).summary).toBe("微電網啟用");
    expect(describeEvent("SOURCE_PAUSED", { reason: "5 failed polls" }).tone).toBe("danger");
    expect(describeEvent("EVIDENCE_CAPTURED", { url: "https://x.test/a", title: null })).toMatchObject({
      label: "擷取證據",
      summary: "https://x.test/a",
    });
  });

  it("stories read with their score as a percentage (T-504)", () => {
    expect(describeEvent("STORY_DISCOVERED", { title: "微電網啟用", score: "0.567" })).toMatchObject({
      label: "發現題材",
      summary: "微電網啟用・分數 57",
    });
    expect(describeEvent("STORY_SELECTED", { title: "A", score: 1 }).tone).toBe("ok");
    expect(describeEvent("STORY_DROPPED", { title: "B", score: 0, reason: "no sources" }).summary).toBe("B・no sources");
  });

  it("claims say their type and how much evidence they quote (T-505)", () => {
    expect(describeEvent("CLAIM_CREATED", { claim_type: "number", evidence_ids: ["a", "b"] }).summary).toBe("數字・引用 2 份證據");
    expect(describeEvent("CLAIM_CREATED", { claim_type: "opinion", evidence_ids: [] }).summary).toBe("意見・尚無證據");
  });

  it("an unknown type is shown as itself", () => {
    expect(describeEvent("SOMETHING_NEW", {})).toEqual({ label: "SOMETHING_NEW", tone: "neutral", summary: null, known: false });
  });
});

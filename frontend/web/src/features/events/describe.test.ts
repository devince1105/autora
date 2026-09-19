import { describe, expect, it } from "vitest";

import { describeEvent } from "./describe";

describe("describeEvent: newsroom sources (T-501)", () => {
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
  });

  it("an unknown type is shown as itself", () => {
    expect(describeEvent("SOMETHING_NEW", {})).toEqual({ label: "SOMETHING_NEW", tone: "neutral", summary: null, known: false });
  });
});

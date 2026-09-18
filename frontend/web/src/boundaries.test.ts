// Proves the ESLint boundary rules actually fire (T-109). If someone loosens eslint.config.mjs,
// these tests fail even though no violating file exists in the tree yet.
import { ESLint } from "eslint";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";

const cwd = fileURLToPath(new URL("..", import.meta.url));
const eslint = new ESLint({ cwd });

// Loading the config and plugins takes seconds when the other test files run alongside; do it once,
// with room, instead of inside the first test's 5 s budget.
beforeAll(async () => {
  await eslint.lintText("export {};", { filePath: "src/warmup.ts" });
}, 60_000);

async function ruleIds(filePath: string, code: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath });
  return (result?.messages ?? []).map((m) => m.ruleId ?? "fatal");
}

describe("office3d is a read-only view", () => {
  it.each([
    'import { client } from "@/api/client";',
    'import { connect } from "@/realtime/client";',
    'import { Panel } from "../features/agent-panel/Panel";',
  ])("rejects %s", async (code) => {
    expect(await ruleIds("src/office3d/agents/AgentAvatar.tsx", `${code}\nexport {};`)).toContain(
      "no-restricted-imports",
    );
  });

  it("rejects network globals", async () => {
    const ids = await ruleIds("src/office3d/scene/Probe.ts", 'export const x = () => fetch("/api");');
    expect(ids).toContain("no-restricted-globals");
  });

  it("allows store selectors and the event schema", async () => {
    const code = 'import { useRealtime } from "@/stores/realtime";\nimport type { EventEnvelope } from "@autora/event-schema";\nexport type T = EventEnvelope; export const u = useRealtime;';
    expect(await ruleIds("src/office3d/agents/AgentAvatar.tsx", code)).not.toContain(
      "no-restricted-imports",
    );
  });
});

describe("realtime does not depend on UI", () => {
  it("rejects importing office3d", async () => {
    const code = 'import { Scene } from "@/office3d/scene/OfficeScene";\nexport const s = Scene;';
    expect(await ruleIds("src/realtime/client.ts", code)).toContain("no-restricted-imports");
  });
});

describe("features use the office only via OfficeCanvas", () => {
  it("allows OfficeCanvas", async () => {
    const code = 'import { OfficeCanvas } from "@/office3d/OfficeCanvas";\nexport const c = OfficeCanvas;';
    expect(await ruleIds("src/features/office/Page.tsx", code)).not.toContain(
      "no-restricted-imports",
    );
  });

  it("rejects office internals", async () => {
    const code = 'import { mapping } from "@/office3d/visual/mapping";\nexport const m = mapping;';
    expect(await ruleIds("src/features/office/Page.tsx", code)).toContain("no-restricted-imports");
  });
});

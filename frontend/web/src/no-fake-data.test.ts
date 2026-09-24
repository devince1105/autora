// AC-S6, the static half: the screen may not invent anything (T-610, risk R1).
//
// Two rules, checked over the source itself rather than over a running page, because the point
// is that the code *cannot* do these things, not that it happened not to on the day we looked:
//
// 1. **No timer changes state.** Everything the office shows comes from agent_activity, tasks
//    and the event stream. A setTimeout in the state layer would be a second source of truth
//    that drifts from the backend — the exact failure R1 names.
// 2. **No roster in the source.** Agents, their names and their avatars come from the snapshot
//    and the events. A hardcoded list would still draw a company that no longer exists.
//
// Animation timers are fine and live in the 3D layer: they move a mesh, never the store.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(process.cwd(), "src");
const CODE = new Set([".ts", ".tsx"]);
const SKIP = /\.test\.tsx?$|__fixtures__|__mocks__|\/api\/schema\.gen\.ts$/;

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (CODE.has(extname(entry)) && !SKIP.test(path)) out.push(path);
  }
  return out;
}

const FILES = sources(ROOT);
const read = (path: string) => readFileSync(path, "utf8");
const rel = (path: string) => relative(ROOT, path);

/** Lines matching `pattern`, as "file:line: text", for the files under `within`. */
function hits(pattern: RegExp, within = ""): string[] {
  const out: string[] = [];
  for (const path of FILES) {
    if (within && !rel(path).startsWith(within)) continue;
    read(path)
      .split("\n")
      .forEach((line, i) => {
        if (pattern.test(line)) out.push(`${rel(path)}:${i + 1}: ${line.trim()}`);
      });
  }
  return out;
}

describe("the screen invents nothing (AC-S6)", () => {
  it("has sources to scan at all", () => {
    expect(FILES.length).toBeGreaterThan(50);
  });

  it("no timer drives state: the store and the reducer contain none", () => {
    const timers = /\b(setTimeout|setInterval|requestAnimationFrame)\s*\(/;
    expect(hits(timers, "stores")).toEqual([]);
    expect(hits(timers, "realtime/reducer.ts")).toEqual([]);
    expect(hits(timers, "realtime/snapshot.ts")).toEqual([]);
    // One exception, by name: after paying, the done page waits and asks the API again whether
    // PAYUNi's notification has arrived (D-034). What it shows is still only what the API says.
    expect(hits(timers, "features").filter((hit) => !hit.startsWith("features/site/PaymentDone.tsx:"))).toEqual([]);
    expect(hits(timers, "features/site/PaymentDone.tsx")).toHaveLength(1);
  });

  it("the socket's own timers are the connection's, and are listed here by name", () => {
    // The client may wait: it reconnects, it acks, it watches for silence. None of those
    // decide what an agent is doing — they decide when to ask the server again. A new timer
    // here fails this test on purpose, so somebody has to say which kind it is.
    const named = hits(/\b(setTimeout|setInterval|requestAnimationFrame)\s*\(/, "realtime/client.ts")
      .map((hit) => hit.split(": ")[1].split(" =")[0].replace("this.", "").trim());
    expect(named.sort()).toEqual(["ackTimer", "reconnectTimer", "watchdog"]);
  });

  it("no agent roster is written down: names and avatars come from the stream", () => {
    // a literal display name, a specific avatar, or a list of agents would draw a company
    // that may no longer exist — the office must draw whoever the snapshot says is there
    expect(hits(/\bdisplay_name\s*:\s*["'`]/)).toEqual([]);
    expect(hits(/\bavatar_key\s*:\s*["'`](?!default["'`])/)).toEqual([]);
    expect(hits(/\b(agents|roster)\s*[:=]\s*\[\s*\{/)).toEqual([]);
  });

  it("the office reads the store and nothing else: no fetch inside the 3D layer", () => {
    expect(hits(/\bfetch\s*\(|\bapi\.GET\s*\(/, "office3d")).toEqual([]);
  });
});

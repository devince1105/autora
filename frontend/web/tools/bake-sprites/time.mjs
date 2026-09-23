// How long the 2D board's first frame takes: every picture painted from an empty cache, in headless
// Chrome with the CPU slowed four times (a slow laptop, or a CI runner). Two styles, five runs each.
//
//     node tools/bake-sprites/time.mjs
//
// This is how the floor textures' first version was caught costing ~10x the frame (D-029): keep
// the number in mind when a change touches ``floorPainter`` or the art it paints.
import { build } from "esbuild";
import { chromium } from "@playwright/test";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
const WEB = resolve(".");
const into = await mkdtemp(join(tmpdir(), "time-"));
await build({ entryPoints: [join(WEB, "tools/bake-sprites/preview.ts")], bundle: true, format: "iife", target: "es2022", outfile: join(into, "render.js"), alias: { "@": join(WEB, "src") }, logLevel: "error" });
await writeFile(join(into, "bake.html"), '<!doctype html><meta charset="utf-8"><script src="./render.js"></script>');
const browser = await chromium.launch({ channel: "chrome", args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage();
await page.route("http://bake.local/**", async (r) => r.fulfill({ body: await readFile(join(into, new URL(r.request().url()).pathname)) }));
await page.goto("http://bake.local/bake.html");
const cdp = await page.context().newCDPSession(page);
await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
for (const theme of ["muji", "cyber"]) {
  const runs = [];
  for (let i = 0; i < 5; i++) runs.push(await page.evaluate((t) => window.timeFirstFrame(t), theme));
  runs.sort((a, b) => a - b);
  console.log(`${process.env.LABEL ?? "first frame"} ${theme}: median ${runs[2].toFixed(0)} ms (runs ${runs.map((r) => r.toFixed(0)).join(", ")})`);
}
await browser.close();

// Bake the 3D office's furniture into the 2D board's sprites (D-026).
//
//     pnpm -F web bake-sprites
//
// The 2D office used to be drawn by hand, sprite by sprite, and the gap between it and the 3D
// office was the gap between two people's ideas of what a desk looks like. Now there is one desk:
// this script renders each piece of ``scene/furniture.ts`` through a real WebGL renderer, from a
// fixed orthographic angle, and writes the result as pixel rows.
//
// It runs in a browser because that is where WebGL is — esbuild bundles the renderer, Playwright
// (already here for the e2e tests) opens the page and calls it, and what comes back is written to
// ``src/office3d/fallback/art/baked.ts``. That file is committed: the site never bakes anything,
// and the 2D board still works with no WebGL at all, which is the whole reason it exists.
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { build } from "esbuild";
import { chromium } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = resolve(HERE, "../..");
const OUT = join(WEB, "src/office3d/fallback/art/baked.ts");

async function bundle(into, entry = "render.ts") {
  await build({
    entryPoints: [join(HERE, entry)],
    bundle: true,
    format: "iife",
    target: "es2022",
    outfile: join(into, "render.js"),
    alias: { "@": join(WEB, "src") },
    logLevel: "warning",
  });
  await writeFile(
    join(into, "bake.html"),
    '<!doctype html><meta charset="utf-8"><body><script src="./render.js"></script></body>',
  );
}

/**
 * Playwright's own Chromium if it is there, otherwise the Chrome already on the machine.
 *
 * The fallback is not a nicety: a developer who has never run the e2e tests has no Playwright
 * browser, and a 200 MB download to redraw a desk is a reason not to redraw the desk. SwiftShader
 * is asked for explicitly because headless Chrome has no GPU to render WebGL with, and a bake that
 * silently produced an empty sprite would be worse than one that refuses to start.
 */
const BROWSER_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"];

async function open() {
  try {
    return await chromium.launch({ args: BROWSER_ARGS });
  } catch (error) {
    if (!String(error).includes("Executable doesn't exist")) throw error;
    console.log("no Playwright Chromium; using the Chrome on this machine");
    return await chromium.launch({ channel: "chrome", args: BROWSER_ARGS });
  }
}

async function renderInABrowser(into) {
  const browser = await open();
  try {
    const page = await browser.newPage();
    const problems = [];
    page.on("pageerror", (error) => problems.push(String(error)));
    page.on("console", (message) => {
      if (message.type() === "error") problems.push(message.text());
    });
    await page.goto(`file://${join(into, "bake.html")}`);
    const sprites = await page.evaluate(() => window.bakeAll());
    if (problems.length) throw new Error(`the browser complained:\n  ${problems.join("\n  ")}`);
    return sprites;
  } finally {
    await browser.close();
  }
}

/** Trim rows and columns that are empty in both the piece and its shadow: a sprite is a shape. */
function trim(sprite) {
  const solid = (row) => /[^.]/.test(sprite.rows[row]) || /[^.]/.test(sprite.shadow[row]);
  const rowIndexes = sprite.rows.map((_, i) => i);
  const top = rowIndexes.findIndex(solid);
  if (top === -1) throw new Error(`${sprite.name} baked to nothing`);
  const bottom = rowIndexes.length - 1 - [...rowIndexes].reverse().findIndex(solid);
  const used = (col) => sprite.rows.some((line, i) => line[col] !== "." || sprite.shadow[i][col] !== ".");
  const columns = Array.from({ length: sprite.w }, (_, col) => used(col));
  const left = columns.indexOf(true);
  const right = columns.lastIndexOf(true);
  const cut = (lines) => lines.slice(top, bottom + 1).map((line) => line.slice(left, right + 1));
  return {
    ...sprite,
    w: right - left + 1,
    h: bottom - top + 1,
    rows: cut(sprite.rows),
    shadow: cut(sprite.shadow),
    anchor: sprite.anchor - top,
    originX: sprite.originX - left,
    originY: sprite.originY - top,
  };
}

function quote(value) {
  return JSON.stringify(value);
}

function emit(sprites) {
  const pieces = sprites
    .map((s) => {
      const keys = Object.entries(s.keys)
        .map(([key, { slot, tone }]) => `      ${quote(key)}: [${quote(slot)}, ${tone}],`)
        .join("\n");
      return `  ${quote(s.name)}: {
    w: ${s.w},
    h: ${s.h},
    anchor: ${s.anchor},
    origin: [${s.originX}, ${s.originY}],
    footprint: [${s.footprint[0]}, ${s.footprint[1]}],
    keys: {
${keys}
    },
    rows: [
${s.rows.map((line) => `      ${quote(line)},`).join("\n")}
    ],
    shadow: [
${s.shadow.map((line) => `      ${quote(line)},`).join("\n")}
    ],
  },`;
    })
    .join("\n");

  return `// Generated by tools/bake-sprites — do not edit by hand (D-026).
//
// Each sprite is one piece of the 3D office (\`scene/furniture.ts\`) rendered from a fixed
// orthographic angle. A character is not a colour: it is a **palette slot and how lit that pixel
// is**, so the same sprite can be painted in any of the office's styles (D-011). \`art/theme.ts\`
// turns a character table into the palette \`drawSprite\` wants.
//
// To change what the 2D office looks like, change the 3D furniture and run:
//
//     pnpm -F web bake-sprites

export interface BakedPiece {
  w: number;
  h: number;
  /** The row of the piece's front-bottom edge, for sorting by depth. */
  anchor: number;
  /** Where the piece's own origin sits inside the sprite, in pixels. */
  origin: readonly [number, number];
  /** Metres of floor it stands on. */
  footprint: readonly [number, number];
  /** character -> [palette slot, how lit: 0 is darkest, 4 lightest, -1 an outline] */
  keys: Record<string, readonly [string, number]>;
  rows: string[];
  /** Where its shadow falls on the floor: \`#\` shadow, \`.\` none. */
  shadow: string[];
}

export const BAKED: Record<string, BakedPiece> = {
${pieces}
};

export type BakedName = keyof typeof BAKED;
`;
}

/**
 * ``--preview <dir>``: instead of writing the sprites, render the contact sheet (``preview.ts``)
 * — a few camera angles and resolutions, and every office style — and save it as PNGs. How the
 * bake should look is a choice made by looking, not by arguing about degrees.
 */
async function preview(into, dir) {
  await bundle(into, "preview.ts");
  const browser = await open();
  try {
    const page = await browser.newPage();
    const problems = [];
    page.on("pageerror", (error) => problems.push(String(error)));
    await page.goto(`file://${join(into, "bake.html")}`);
    const sheets = await page.evaluate(() => window.previewSheets());
    if (problems.length) throw new Error(`the browser complained:\n  ${problems.join("\n  ")}`);
    const names = ["angles.png", "themes.png"];
    for (const [i, url] of sheets.entries()) {
      await writeFile(join(dir, names[i]), Buffer.from(url.split(",")[1], "base64"));
      console.log(`wrote ${join(dir, names[i])}`);
    }
  } finally {
    await browser.close();
  }
}

const into = await mkdtemp(join(tmpdir(), "autora-bake-"));
try {
  const previewAt = process.argv.indexOf("--preview");
  if (previewAt !== -1) {
    await preview(into, resolve(process.argv[previewAt + 1] ?? "."));
  } else {
    await bundle(into);
    const sprites = (await renderInABrowser(into)).map(trim);
    const generated = emit(sprites);
    if (process.argv.includes("--check")) {
      const committed = await readFile(OUT, "utf8").catch(() => "");
      if (committed !== generated) throw new Error("the baked sprites are stale: run `pnpm -F web bake-sprites`");
      console.log("baked sprites are up to date");
    } else {
      await writeFile(OUT, generated);
      const total = sprites.reduce((n, s) => n + s.w * s.h, 0);
      console.log(`baked ${sprites.length} pieces, ${total} pixels -> ${OUT.replace(`${WEB}/`, "")}`);
      for (const s of sprites) console.log(`  ${s.name.padEnd(13)} ${s.w}x${s.h}  ${Object.keys(s.keys).length} tones`);
    }
  }
} finally {
  await rm(into, { recursive: true, force: true });
}

// Floor textures drawn in code (T-402, T-413): wood planks, tiles, stone slabs and carpet tiles,
// 256 px, no downloads (04 §7: "zero textures" meant no image assets; these are generated when
// the scene is built). Where there is no 2D canvas (jsdom tests) they are null: plain colour.
import { CanvasTexture, RepeatWrapping, SRGBColorSpace, type Texture } from "three";

import type { FloorLook } from "../palette";

function paint(size: number, draw: (ctx: CanvasRenderingContext2D, size: number) => void): Texture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return null;
  draw(ctx, size);
  const texture = new CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = RepeatWrapping;
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

/** One metre of planks: four rows, staggered seams, a slight tone per plank. */
export function woodTexture(base: string): Texture | null {
  return paint(256, (ctx, size) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, size, size);
    const rows = 4;
    const h = size / rows;
    let seed = 7;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let r = 0; r < rows; r++) {
      let x = -rand() * size * 0.5;
      while (x < size) {
        const w = size * (0.45 + rand() * 0.4);
        ctx.fillStyle = `rgba(${rand() < 0.5 ? "0,0,0" : "255,255,255"},${0.03 + rand() * 0.06})`;
        ctx.fillRect(x, r * h, w, h);
        ctx.fillStyle = "rgba(60,35,15,0.35)";
        ctx.fillRect(x, r * h, 2, h);
        x += w;
      }
      ctx.fillStyle = "rgba(60,35,15,0.4)";
      ctx.fillRect(0, r * h, size, 2);
    }
  });
}

/** Half a metre of tiles: 2 x 2 with grout lines. */
export function tileTexture(base: string, grout: string): Texture | null {
  return paint(256, (ctx, size) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = grout;
    for (let i = 0; i < 2; i++) {
      ctx.fillRect((i * size) / 2, 0, 3, size);
      ctx.fillRect(0, (i * size) / 2, size, 3);
    }
  });
}

/** A metre of stone: 2 x 2 large slabs, each a shade apart, thin light joints. */
export function stoneTexture(base: string): Texture | null {
  return paint(256, (ctx, size) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, size, size);
    const half = size / 2;
    const shades = [0.0, 0.035, 0.02, 0.05];
    shades.forEach((a, i) => {
      ctx.fillStyle = `rgba(0,0,0,${a})`;
      ctx.fillRect((i % 2) * half, Math.floor(i / 2) * half, half, half);
    });
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    for (let i = 0; i < 2; i++) {
      ctx.fillRect(i * half, 0, 2, size);
      ctx.fillRect(0, i * half, size, 2);
    }
  });
}

/** A metre of carpet tiles: 2 x 2, the pile running across in alternate tiles, fine speckle. */
export function carpetTexture(base: string): Texture | null {
  return paint(256, (ctx, size) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, size, size);
    let seed = 13;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const half = size / 2;
    for (let i = 0; i < 4; i++) {
      const x0 = (i % 2) * half;
      const y0 = Math.floor(i / 2) * half;
      const across = (i === 0 || i === 3);
      ctx.fillStyle = "rgba(0,0,0,0.045)";
      for (let k = 0; k < half; k += 4) {
        if (across) ctx.fillRect(x0, y0 + k, half, 1);
        else ctx.fillRect(x0 + k, y0, 1, half);
      }
    }
    for (let k = 0; k < 1400; k++) {
      ctx.fillStyle = rand() < 0.5 ? "rgba(0,0,0,0.07)" : "rgba(255,255,255,0.08)";
      ctx.fillRect(rand() * size, rand() * size, 1.5, 1.5);
    }
    ctx.fillStyle = "rgba(0,0,0,0.08)";
    for (let i = 0; i < 2; i++) {
      ctx.fillRect(i * half, 0, 1, size);
      ctx.fillRect(0, i * half, size, 1);
    }
  });
}

/** A metre of glowing grid: a line on each edge, a faint one through the middle. */
export function gridTexture(base: string, lines: string): Texture | null {
  return paint(256, (ctx, size) => {
    ctx.fillStyle = base;
    ctx.fillRect(0, 0, size, size);
    ctx.fillStyle = lines;
    ctx.fillRect(0, 0, size, 3);
    ctx.fillRect(0, 0, 3, size);
    ctx.globalAlpha = 0.35;
    ctx.fillRect(0, size / 2, size, 1);
    ctx.fillRect(size / 2, 0, 1, size);
    ctx.globalAlpha = 1;
  });
}

/** The texture a floor look asks for (null: none, or no 2D canvas here). */
export function floorTexture(look: FloorLook): Texture | null {
  switch (look.texture) {
    case "wood":
      return woodTexture(look.color);
    case "tile":
      return tileTexture(look.color, "#d6d8db");
    case "stone":
      return stoneTexture(look.color);
    case "carpet":
      return carpetTexture(look.color);
    case "grid":
      return gridTexture(look.color, look.lines ?? "#19e6ff");
    default:
      return null;
  }
}

// Floor textures drawn in code (T-402): wood planks and tiles, 256 px, no downloads (04 §7:
// "zero textures" meant no image assets; these are generated once per load).
import { CanvasTexture, RepeatWrapping, SRGBColorSpace, type Texture } from "three";

function paint(size: number, draw: (ctx: CanvasRenderingContext2D, size: number) => void): Texture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  draw(canvas.getContext("2d")!, size);
  const texture = new CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = RepeatWrapping;
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

/** One metre of planks: four rows, staggered seams, a slight tone per plank. */
export function woodTexture(base: string): Texture {
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
export function tileTexture(base: string, grout: string): Texture {
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

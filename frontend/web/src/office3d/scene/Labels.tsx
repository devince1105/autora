// Zone and room names (T-413): department names painted on the walkway in front of each zone,
// signs on the glass fronts and over the entrance. All labels share one texture (a column of
// rows, one per label) and one mesh: one draw call. In a theme with neon they glow (unlit). No
// 2D canvas (jsdom tests): no labels.
import { useEffect, useMemo } from "react";
import { BufferGeometry, CanvasTexture, Float32BufferAttribute, SRGBColorSpace, type Texture } from "three";

import type { Palette } from "../palette";
import { LABELS, type Label } from "./layout";

const ROW_W = 512;
const ROW_H = ROW_W / 4;
/** Just above the rugs (the top floor layer). */
const FLOOR_Y = 0.016;
const FONT = '"PingFang TC", "Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif';

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** One row per label: a sign gets a plate, a floor label only its lettering. */
export function labelAtlas(labels: readonly Label[], palette: Palette): Texture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = ROW_W;
  canvas.height = ROW_H * labels.length;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    ctx = null;
  }
  if (!ctx) return null;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  labels.forEach((label, i) => {
    const top = i * ROW_H;
    const sign = label.kind === "sign";
    if (sign) {
      ctx.fillStyle = palette.sign;
      roundRect(ctx, 4, top + 4, ROW_W - 8, ROW_H - 8, 14);
      ctx.fill();
    }
    ctx.fillStyle = sign ? palette.signText : palette.floorText;
    ctx.globalAlpha = sign ? 1 : 0.88;
    ctx.font = `600 ${label.text.length > 4 ? 52 : 60}px ${FONT}`;
    ctx.fillText(label.text, ROW_W / 2, top + 70, ROW_W - 40);
    ctx.font = `500 22px ${FONT}`;
    ctx.globalAlpha = sign ? 0.85 : 0.72;
    ctx.fillText(label.sub.split("").join(" "), ROW_W / 2, top + 106, ROW_W - 40);
    ctx.globalAlpha = 1;
  });
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.anisotropy = 8;
  return texture;
}

/** A quad per label, its uvs on the label's row. */
export function labelGeometry(labels: readonly Label[]): BufferGeometry {
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  labels.forEach((label, i) => {
    const w = label.width / 2;
    const h = label.width / 8;
    const [x, y, z] = label.at;
    // corners: top-left, top-right, bottom-right, bottom-left (as read)
    let corners: [number, number, number][];
    if (label.kind === "floor") {
      // read from the front: the top of the text towards the back wall
      const fy = y + FLOOR_Y;
      corners = [
        [x - w, fy, z - h],
        [x + w, fy, z - h],
        [x + w, fy, z + h],
        [x - w, fy, z + h],
      ];
    } else if (label.facing === "x") {
      corners = [
        [x, y + h, z + w],
        [x, y + h, z - w],
        [x, y - h, z - w],
        [x, y - h, z + w],
      ];
    } else {
      corners = [
        [x - w, y + h, z],
        [x + w, y + h, z],
        [x + w, y - h, z],
        [x - w, y - h, z],
      ];
    }
    const v0 = 1 - i / labels.length;
    const v1 = 1 - (i + 1) / labels.length;
    const base = positions.length / 3;
    for (const c of corners) positions.push(...c);
    uvs.push(0, v0, 1, v0, 1, v1, 0, v1);
    indices.push(base, base + 3, base + 1, base + 1, base + 3, base + 2);
  });
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

export function Labels({ palette }: { palette: Palette }) {
  const map = useMemo(() => labelAtlas(LABELS, palette), [palette]);
  const geometry = useMemo(() => labelGeometry(LABELS), []);
  useEffect(() => () => map?.dispose(), [map]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  if (!map) return null;
  return (
    <mesh geometry={geometry} renderOrder={2} receiveShadow>
      {palette.glow.length ? (
        <meshBasicMaterial map={map} transparent depthWrite={false} toneMapped={false} polygonOffset polygonOffsetFactor={-2} />
      ) : (
        <meshStandardMaterial map={map} transparent depthWrite={false} roughness={0.8} polygonOffset polygonOffsetFactor={-2} />
      )}
    </mesh>
  );
}

// A tiny modelling kit (T-402, D-010): furniture is written as lists of coloured primitives, placed
// in the room, and merged into one vertex-coloured mesh. The static office then costs one draw
// call (plus its shadow pass) however much detail it has — no downloaded models, no textures.
import {
  BoxGeometry,
  BufferGeometry,
  Color,
  ConeGeometry,
  CylinderGeometry,
  Euler,
  Float32BufferAttribute,
  IcosahedronGeometry,
  Matrix4,
  Quaternion,
  SphereGeometry,
  Vector3,
} from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";

export type Shape =
  | { kind: "box"; w: number; h: number; d: number }
  | { kind: "cyl"; rTop: number; rBottom: number; h: number; segments: number }
  | { kind: "cone"; r: number; h: number; segments: number }
  | { kind: "ico"; r: number; detail: number }
  | { kind: "sphere"; r: number; segments: number };

export interface Part {
  shape: Shape;
  matrix: Matrix4;
  color: string;
}

type V3 = readonly [number, number, number];

function transform(pos: V3, rot: V3 = [0, 0, 0], scale: V3 = [1, 1, 1]): Matrix4 {
  return new Matrix4().compose(new Vector3(...pos), new Quaternion().setFromEuler(new Euler(...rot)), new Vector3(...scale));
}

/** A box by its centre. */
export const box = (w: number, h: number, d: number, pos: V3, color: string, rot?: V3): Part => ({
  shape: { kind: "box", w, h, d },
  matrix: transform(pos, rot),
  color,
});

/** A box standing on `y` (its bottom), centred on x/z — most furniture reads easier this way. */
export const block = (w: number, h: number, d: number, [x, y, z]: V3, color: string, rot?: V3): Part =>
  box(w, h, d, [x, y + h / 2, z], color, rot);

export const cyl = (rTop: number, rBottom: number, h: number, [x, y, z]: V3, color: string, segments = 10, rot?: V3): Part => ({
  shape: { kind: "cyl", rTop, rBottom, h, segments },
  matrix: transform([x, y + h / 2, z], rot),
  color,
});

export const cone = (r: number, h: number, pos: V3, color: string, rot?: V3, segments = 8): Part => ({
  shape: { kind: "cone", r, h, segments },
  matrix: transform(pos, rot),
  color,
});

export const ico = (r: number, pos: V3, color: string, scale?: V3, detail = 0): Part => ({
  shape: { kind: "ico", r, detail },
  matrix: transform(pos, [0, 0, 0], scale),
  color,
});

export const sphere = (r: number, pos: V3, color: string, scale?: V3, segments = 8): Part => ({
  shape: { kind: "sphere", r, segments },
  matrix: transform(pos, [0, 0, 0], scale),
  color,
});

/** Move a piece built around the origin to (x, z), turned by rotY, raised by y. */
export function place(parts: Part[], x: number, z: number, rotY = 0, y = 0): Part[] {
  const m = transform([x, y, z], [0, rotY, 0]);
  return parts.map((p) => ({ ...p, matrix: m.clone().multiply(p.matrix) }));
}

function geometryOf(shape: Shape): BufferGeometry {
  switch (shape.kind) {
    case "box":
      return new BoxGeometry(shape.w, shape.h, shape.d);
    case "cyl":
      return new CylinderGeometry(shape.rTop, shape.rBottom, shape.h, shape.segments);
    case "cone":
      return new ConeGeometry(shape.r, shape.h, shape.segments);
    case "ico":
      return new IcosahedronGeometry(shape.r, shape.detail);
    case "sphere":
      return new SphereGeometry(shape.r, shape.segments, Math.max(4, Math.round(shape.segments * 0.66)));
  }
}

/** Merge parts into one geometry with a colour per vertex (for a vertexColors material). */
export function buildGeometry(parts: readonly Part[]): BufferGeometry {
  const color = new Color();
  const pieces = parts.map((part) => {
    const source = geometryOf(part.shape);
    const g = source.index ? source.toNonIndexed() : source;
    if (g !== source) source.dispose();
    g.applyMatrix4(part.matrix);
    color.set(part.color); // hex strings are sRGB; Color stores linear, as the renderer expects
    const n = g.getAttribute("position").count;
    const colors = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) colors.set([color.r, color.g, color.b], i * 3);
    g.setAttribute("color", new Float32BufferAttribute(colors, 3));
    g.deleteAttribute("uv");
    return g;
  });
  const merged = mergeGeometries(pieces, false);
  pieces.forEach((g) => g.dispose());
  if (!merged) throw new Error("could not merge the office geometry");
  merged.computeBoundingBox();
  merged.computeBoundingSphere();
  return merged;
}

export function triangleCount(geometry: BufferGeometry): number {
  return (geometry.index ? geometry.index.count : geometry.getAttribute("position").count) / 3;
}

// Status you can read from across the room (T-406, 04 §3): each desk's two screens, its lamp
// (shade + a glow on the desk) and a tag over each head (name, badge, bubble). Screens, shades and
// glows are three instanced meshes whose colours are set every frame from the visual tracker;
// the tags' text is written into the DOM when the tracker's version moves. None of it re-renders
// React on a store change.
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  AdditiveBlending,
  CanvasTexture,
  CircleGeometry,
  Color,
  ConeGeometry,
  InstancedMesh,
  MeshBasicMaterial,
  Object3D,
  PlaneGeometry,
} from "three";

import { ROLE_COLOR } from "../palette";
import { lampSpot, MONITOR, screenSpots } from "../scene/furniture";
import { allSeats, type Seat } from "../scene/layout";
import { useVisualTracker } from "../visual/tracker";
import { applyTag, lampColors, screenColor } from "./indicators";
import { useRoster, type Member } from "./roster";

const SEATS = allSeats();

function instanced(geometry: ConstructorParameters<typeof InstancedMesh>[0], material: MeshBasicMaterial, placements: { pos: readonly number[]; rot?: [number, number, number] }[]) {
  const mesh = new InstancedMesh(geometry, material, placements.length);
  const dummy = new Object3D();
  placements.forEach((p, i) => {
    dummy.position.set(p.pos[0], p.pos[1], p.pos[2]);
    dummy.rotation.set(...(p.rot ?? [0, 0, 0]));
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    mesh.setColorAt(i, new Color(0, 0, 0));
  });
  mesh.frustumCulled = false;
  return mesh;
}

/** A soft round spot for the lamp glow (none where there is no 2D canvas, e.g. tests). */
function glowTexture(): CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 64, 64);
  return new CanvasTexture(canvas);
}

/** Screens and lamps of every desk; an empty desk's are off. */
export function DeskStatus() {
  const tracker = useVisualTracker();
  const { bySeat } = useRoster();

  const meshes = useMemo(() => {
    const screenPlaces = SEATS.flatMap((seat) => screenSpots(seat).map((s) => ({ seat: seat.key, pos: s.pos, rot: [0, s.rotY, 0] as [number, number, number] })));
    const lampPlaces = SEATS.map((seat) => ({ seat: seat.key, ...lampSpot(seat) }));
    const glowMap = glowTexture();
    return {
      screenSeats: screenPlaces.map((p) => p.seat),
      lampSeats: lampPlaces.map((p) => p.seat),
      screens: instanced(new PlaneGeometry(MONITOR.width - 0.06, MONITOR.height - 0.06), new MeshBasicMaterial({ toneMapped: false }), screenPlaces),
      shades: instanced(new ConeGeometry(0.09, 0.1, 12, 1, true), new MeshBasicMaterial({ toneMapped: false }), lampPlaces.map((p) => ({ pos: p.shade }))),
      glows: instanced(
        new CircleGeometry(0.6, 24),
        new MeshBasicMaterial({ map: glowMap, transparent: true, blending: AdditiveBlending, depthWrite: false, toneMapped: false }),
        lampPlaces.map((p) => ({ pos: p.glow, rot: [-Math.PI / 2, 0, 0] })),
      ),
    };
  }, []);
  useEffect(
    () => () => {
      for (const mesh of [meshes.screens, meshes.shades, meshes.glows]) {
        mesh.geometry.dispose();
        const material = mesh.material as MeshBasicMaterial;
        material.map?.dispose();
        material.dispose();
        mesh.dispose();
      }
    },
    [meshes],
  );

  const color = useRef({ screen: new Color(), shade: new Color(), glow: new Color() });
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const c = color.current;
    const visualAt = (seatKey: string) => {
      const agentId = bySeat.get(seatKey);
      return agentId ? tracker.get(agentId) : undefined;
    };
    meshes.screenSeats.forEach((seat, i) => meshes.screens.setColorAt(i, screenColor(visualAt(seat)?.screen ?? "off", t, c.screen)));
    meshes.lampSeats.forEach((seat, i) => {
      const { shade, glow } = lampColors(visualAt(seat)?.deskLight ?? "off", t, c.shade, c.glow);
      meshes.shades.setColorAt(i, shade);
      meshes.glows.setColorAt(i, glow);
    });
    for (const mesh of [meshes.screens, meshes.shades, meshes.glows]) mesh.instanceColor!.needsUpdate = true;
  });

  return (
    <>
      <primitive object={meshes.screens} />
      <primitive object={meshes.shades} />
      <primitive object={meshes.glows} />
    </>
  );
}

function HeadTag({ member, seat }: { member: Member; seat: Seat }) {
  const tracker = useVisualTracker();
  const badge = useRef<HTMLSpanElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  const seen = useRef(-1);
  useFrame(() => {
    if (tracker.version === seen.current || !badge.current) return;
    seen.current = tracker.version;
    applyTag({ badge: badge.current, bubble: bubble.current }, tracker.get(member.id));
  });
  return (
    <Html position={[seat.chair[0], 2.05, seat.chair[1]]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <div className="flex flex-col items-center gap-0.5" data-testid={`head-tag-${member.id}`}>
        <span className="flex items-center gap-1 rounded-full bg-surface/90 py-0.5 pr-1 pl-2 text-xs font-medium whitespace-nowrap text-ink shadow">
          <span aria-hidden className="size-2 rounded-full" style={{ backgroundColor: ROLE_COLOR[member.role] ?? ROLE_COLOR.spare }} />
          {member.name}
          <span ref={badge} className="hidden" />
        </span>
        <span ref={bubble} hidden className="max-w-40 truncate rounded-md bg-surface/80 px-1.5 text-[11px] text-muted italic shadow-sm" />
      </div>
    </Html>
  );
}

/** A tag over every seated agent. */
export function HeadTags() {
  const { members, seats } = useRoster();
  return (
    <>
      {members.map((member) => {
        const seat = seats.get(member.id);
        return seat ? <HeadTag key={member.id} member={member} seat={seat} /> : null;
      })}
    </>
  );
}

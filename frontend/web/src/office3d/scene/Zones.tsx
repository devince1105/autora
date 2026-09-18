// Area rugs (T-402): one soft colour per zone under its desks, and one under the approval desk.
import { ZONE_COLOR } from "../palette";
import { allSeats, APPROVAL_DESK, DESK, type ZoneId } from "./layout";

const PAD = 0.7;

interface Rug {
  zone: ZoneId | "approval";
  x: number;
  z: number;
  width: number;
  depth: number;
}

/** One rug per zone, covering its desks and chairs with some margin. */
export function rugs(): Rug[] {
  const byZone = new Map<ZoneId, { minX: number; maxX: number; minZ: number; maxZ: number }>();
  for (const seat of allSeats()) {
    const [x, z] = seat.desk;
    const box = byZone.get(seat.zone) ?? { minX: Infinity, maxX: -Infinity, minZ: Infinity, maxZ: -Infinity };
    box.minX = Math.min(box.minX, x - DESK.width / 2);
    box.maxX = Math.max(box.maxX, x + DESK.width / 2);
    box.minZ = Math.min(box.minZ, z - DESK.depth / 2);
    box.maxZ = Math.max(box.maxZ, seat.chair[1] + 0.3);
    byZone.set(seat.zone, box);
  }
  const zones: Rug[] = [...byZone].map(([zone, b]) => ({
    zone,
    x: (b.minX + b.maxX) / 2,
    z: (b.minZ + b.maxZ) / 2,
    width: b.maxX - b.minX + PAD,
    depth: b.maxZ - b.minZ + PAD,
  }));
  const [ax, az] = APPROVAL_DESK.center;
  zones.push({ zone: "approval", x: ax, z: az, width: APPROVAL_DESK.width + 1.4, depth: APPROVAL_DESK.depth + 1.8 });
  return zones;
}

export function Zones() {
  return (
    <group>
      {rugs().map((rug) => (
        <mesh key={rug.zone} rotation-x={-Math.PI / 2} position={[rug.x, 0.005, rug.z]}>
          <planeGeometry args={[rug.width, rug.depth]} />
          <meshStandardMaterial color={ZONE_COLOR[rug.zone]} />
        </mesh>
      ))}
    </group>
  );
}

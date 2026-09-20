// Which business a room works for (ARCHITECTURE_V2 §14.7, T-600): a coloured band along the
// front edge of each occupied department, so "how many businesses does this company run" is a
// glance rather than a query.
//
// It is drawn from the roster and nothing else: a room gets a band when somebody works in it,
// in the colour of the business they work for, and no band at all when their department belongs
// to no business — a company-wide function (Finance, the Executive) is not a business and the
// floor should not pretend it is.
import { useMemo } from "react";

import { businessColors } from "../palette";
import { useRoster } from "../agents/roster";
import { ZONES, type ZoneId } from "./layout";

/** How thick the band is and how far it sits inside the room's edge, in metres. */
const BAND = { depth: 0.16, inset: 0.05, height: 0.012 } as const;

export interface Band {
  zone: ZoneId;
  business: string;
  color: string;
  /** Centre of the band and its size, in metres. */
  at: [number, number];
  width: number;
}

interface Placed {
  office_zone_key: string | null;
  business_unit_key: string | null;
}

/**
 * One band per room that a business works in.
 *
 * A room with people from two businesses takes the first by key, which is a compromise the
 * floor plan forces: a desk belongs to one room, and two businesses sharing a room is an
 * organisation question, not a colour question.
 */
export function bandsFor(agents: readonly Placed[]): Band[] {
  const colors = businessColors(agents.map((a) => a.business_unit_key));
  const byZone = new Map<string, string>();
  for (const agent of agents) {
    const zone = agent.office_zone_key;
    if (!zone || !agent.business_unit_key || !(zone in ZONES)) continue;
    const already = byZone.get(zone);
    if (already === undefined || agent.business_unit_key < already) {
      byZone.set(zone, agent.business_unit_key);
    }
  }
  return [...byZone]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([zone, business]) => {
      const area = ZONES[zone as keyof typeof ZONES];
      // the work row opens to the front, the rest to the back: the band sits on that edge
      const opensForward = area.maxZ <= 2;
      const z = opensForward ? area.maxZ - BAND.inset - BAND.depth / 2 : area.minZ + BAND.inset + BAND.depth / 2;
      return {
        zone: zone as ZoneId,
        business,
        color: colors[business],
        at: [(area.minX + area.maxX) / 2, z] as [number, number],
        width: area.maxX - area.minX - 0.6,
      };
    });
}

export function BusinessBands() {
  const { members } = useRoster();
  const bands = useMemo(
    () =>
      bandsFor(
        members.map((m) => ({
          office_zone_key: m.office_zone_key,
          business_unit_key: m.business_unit,
        })),
      ),
    [members],
  );
  return (
    <group>
      {bands.map((band) => (
        <mesh
          key={band.zone}
          rotation-x={-Math.PI / 2}
          position={[band.at[0], BAND.height, band.at[1]]}
          userData={{ businessBand: band.business }}
        >
          <planeGeometry args={[band.width, BAND.depth]} />
          <meshStandardMaterial color={band.color} roughness={0.6} />
        </mesh>
      ))}
    </group>
  );
}

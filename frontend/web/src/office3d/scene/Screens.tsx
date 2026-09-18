// Every seat's two screens, instanced (one draw call). Dark for now; T-406 colours each
// instance from the seated agent's visual state.
import { Instance, Instances } from "@react-three/drei";

import { PALETTE } from "../palette";
import { MONITOR, screenSpots } from "./furniture";
import { allSeats } from "./layout";

const spots = allSeats().flatMap(screenSpots);

export function Screens() {
  return (
    <Instances limit={spots.length} range={spots.length}>
      <planeGeometry args={[MONITOR.width - 0.06, MONITOR.height - 0.06]} />
      <meshBasicMaterial color={PALETTE.screenOff} toneMapped={false} />
      {spots.map((spot, i) => (
        <Instance key={i} position={spot.pos} rotation-y={spot.rotY} />
      ))}
    </Instances>
  );
}

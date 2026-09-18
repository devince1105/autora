// Assembles the office (04 §1). Avatars, screens, status lights and the courier join in T-405+.
import { PALETTE } from "../palette";
import { StatsProbe } from "../perf/StatsProbe";
import { Furniture } from "./Furniture";
import { Room } from "./Room";
import { Zones } from "./Zones";

export function OfficeScene() {
  return (
    <>
      <color attach="background" args={[PALETTE.sky]} />
      <hemisphereLight args={[PALETTE.skyLight, PALETTE.groundLight, 1.6]} />
      <directionalLight position={[8, 14, 6]} intensity={1.2} />
      <Room />
      <Zones />
      <Furniture />
      <StatsProbe />
    </>
  );
}

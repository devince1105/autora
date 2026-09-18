// Desks, chairs and monitors for every seat (T-402), instanced: one draw call per part, however
// many desks (04 §7). Screens are dark here; T-406 lights them per agent.
import { Instance, Instances } from "@react-three/drei";

import { PALETTE } from "../palette";
import { allSeats, CHAIR, DESK } from "./layout";

const seats = allSeats();
const n = seats.length;

export function Furniture() {
  return (
    <group>
      {/* desk tops and the two side panels of each desk */}
      <Instances limit={n} range={n}>
        <boxGeometry args={[DESK.width, 0.06, DESK.depth]} />
        <meshStandardMaterial color={PALETTE.deskTop} />
        {seats.map((s) => (
          <Instance key={s.key} position={[s.desk[0], DESK.height, s.desk[1]]} />
        ))}
      </Instances>
      <Instances limit={n * 2} range={n * 2}>
        <boxGeometry args={[0.06, DESK.height, DESK.depth - 0.1]} />
        <meshStandardMaterial color={PALETTE.deskLeg} />
        {seats.flatMap((s) =>
          [-1, 1].map((side) => (
            <Instance key={`${s.key}:${side}`} position={[s.desk[0] + side * (DESK.width / 2 - 0.08), DESK.height / 2, s.desk[1]]} />
          )),
        )}
      </Instances>

      {/* chairs: seat and back (the back is behind the sitter, on the +z side) */}
      <Instances limit={n} range={n}>
        <boxGeometry args={[CHAIR.size, 0.1, CHAIR.size]} />
        <meshStandardMaterial color={PALETTE.chair} />
        {seats.map((s) => (
          <Instance key={s.key} position={[s.chair[0], 0.45, s.chair[1]]} />
        ))}
      </Instances>
      <Instances limit={n} range={n}>
        <boxGeometry args={[CHAIR.size, 0.55, 0.08]} />
        <meshStandardMaterial color={PALETTE.chairBack} />
        {seats.map((s) => (
          <Instance key={s.key} position={[s.chair[0], 0.75, s.chair[1] + CHAIR.size / 2 - 0.04]} />
        ))}
      </Instances>
      <Instances limit={n} range={n}>
        <cylinderGeometry args={[0.05, 0.05, 0.4, 8]} />
        <meshStandardMaterial color={PALETTE.deskLeg} />
        {seats.map((s) => (
          <Instance key={s.key} position={[s.chair[0], 0.2, s.chair[1]]} />
        ))}
      </Instances>

      {/* monitors: body and (for now dark) screen, facing the sitter (+z) */}
      <Instances limit={n} range={n}>
        <boxGeometry args={[0.7, 0.45, 0.05]} />
        <meshStandardMaterial color={PALETTE.monitor} />
        {seats.map((s) => (
          <Instance key={s.key} position={[s.screen[0], s.screen[1], s.screen[2] - 0.03]} />
        ))}
      </Instances>
      <Instances limit={n} range={n}>
        <planeGeometry args={[0.62, 0.37]} />
        <meshStandardMaterial color={PALETTE.screenOff} />
        {seats.map((s) => (
          <Instance key={s.key} position={s.screen as [number, number, number]} />
        ))}
      </Instances>
    </group>
  );
}

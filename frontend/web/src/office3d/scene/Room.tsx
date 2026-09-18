// The room itself (T-402): floor, the back and left walls (the camera looks from the front
// right), a window, the CEO's glass office with its door, the meeting table, the approval desk
// and a few plants. Decoration only: nothing here carries state.
import { PALETTE } from "../palette";
import { APPROVAL_DESK, CEO_OFFICE, MEETING_TABLE, ROOM } from "./layout";

const WALL = 0.2;
const width = ROOM.maxX - ROOM.minX;
const depth = ROOM.maxZ - ROOM.minZ;
const cx = (ROOM.minX + ROOM.maxX) / 2;
const cz = (ROOM.minZ + ROOM.maxZ) / 2;

function Plant({ x, z, scale = 1 }: { x: number; z: number; scale?: number }) {
  return (
    <group position={[x, 0, z]} scale={scale}>
      <mesh position={[0, 0.25, 0]}>
        <cylinderGeometry args={[0.28, 0.22, 0.5, 12]} />
        <meshStandardMaterial color={PALETTE.pot} />
      </mesh>
      <mesh position={[0, 0.85, 0]}>
        <icosahedronGeometry args={[0.45, 1]} />
        <meshStandardMaterial color={PALETTE.leaf} flatShading />
      </mesh>
    </group>
  );
}

function Glass() {
  const { minX, maxX, minZ, maxZ, doorX, doorWidth } = CEO_OFFICE;
  const h = 2.2;
  const leftEnd = doorX - doorWidth / 2;
  const rightStart = doorX + doorWidth / 2;
  const panes: [number, number, number, number][] = [
    // [x, z, width along x, depth along z]
    [(minX + leftEnd) / 2, maxZ, leftEnd - minX, 0.05],
    [(rightStart + maxX) / 2, maxZ, maxX - rightStart, 0.05],
    [maxX, (minZ + maxZ) / 2, 0.05, maxZ - minZ],
  ];
  return (
    <group>
      {panes.map(([x, z, w, d]) => (
        <mesh key={`${x},${z}`} position={[x, h / 2, z]}>
          <boxGeometry args={[w, h, d]} />
          <meshStandardMaterial color={PALETTE.glass} transparent opacity={0.28} depthWrite={false} />
        </mesh>
      ))}
    </group>
  );
}

export function Room() {
  const [tx, tz] = MEETING_TABLE.center;
  const [ax, az] = APPROVAL_DESK.center;
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position={[cx, 0, cz]}>
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial color={PALETTE.floor} />
      </mesh>
      {/* back wall (-z) and left wall (-x), with a trim along the floor */}
      <mesh position={[cx, ROOM.wallHeight / 2, ROOM.minZ - WALL / 2]}>
        <boxGeometry args={[width + WALL * 2, ROOM.wallHeight, WALL]} />
        <meshStandardMaterial color={PALETTE.wall} />
      </mesh>
      <mesh position={[ROOM.minX - WALL / 2, ROOM.wallHeight / 2, cz]}>
        <boxGeometry args={[WALL, ROOM.wallHeight, depth]} />
        <meshStandardMaterial color={PALETTE.wall} />
      </mesh>
      <mesh position={[cx, 0.08, ROOM.minZ + 0.02]}>
        <boxGeometry args={[width, 0.16, 0.04]} />
        <meshStandardMaterial color={PALETTE.wallTrim} />
      </mesh>
      <mesh position={[ROOM.minX + 0.02, 0.08, cz]}>
        <boxGeometry args={[0.04, 0.16, depth]} />
        <meshStandardMaterial color={PALETTE.wallTrim} />
      </mesh>
      {/* a wide window on the back wall, over the meeting area */}
      <mesh position={[5, 1.7, ROOM.minZ + 0.01]}>
        <boxGeometry args={[6.2, 1.5, 0.04]} />
        <meshStandardMaterial color={PALETTE.windowFrame} />
      </mesh>
      <mesh position={[5, 1.7, ROOM.minZ + 0.04]}>
        <boxGeometry args={[5.9, 1.25, 0.02]} />
        <meshStandardMaterial color={PALETTE.window} emissive={PALETTE.window} emissiveIntensity={0.35} />
      </mesh>

      <Glass />

      {/* meeting table and the approval desk */}
      <mesh position={[tx, 0.72, tz]}>
        <boxGeometry args={[MEETING_TABLE.width, 0.08, MEETING_TABLE.depth]} />
        <meshStandardMaterial color={PALETTE.meetingTable} />
      </mesh>
      <mesh position={[tx, 0.36, tz]}>
        <cylinderGeometry args={[0.18, 0.3, 0.72, 10]} />
        <meshStandardMaterial color={PALETTE.deskLeg} />
      </mesh>
      <mesh position={[ax, 0.45, az]}>
        <boxGeometry args={[APPROVAL_DESK.width, 0.9, APPROVAL_DESK.depth]} />
        <meshStandardMaterial color={PALETTE.approvalDesk} />
      </mesh>

      <Plant x={ROOM.minX + 0.6} z={ROOM.maxZ - 0.6} />
      <Plant x={ROOM.maxX - 0.6} z={ROOM.minZ + 0.6} scale={1.2} />
      <Plant x={-2.6} z={ROOM.minZ + 0.6} scale={0.9} />
      <Plant x={ROOM.maxX - 0.6} z={ROOM.maxZ - 0.6} scale={0.8} />
    </group>
  );
}

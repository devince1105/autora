// A ring on the floor under the selected agent, in its role colour (T-409, 04 §5). It follows
// the avatar (walks too) and is read from the UI store every frame, not through React.
import { useFrame, useThree } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import { Color, Vector3, type Mesh, type MeshBasicMaterial, type Object3D } from "three";

import { realtimeStore } from "@/stores/realtime";
import { uiStore } from "@/stores/ui";

import { findAvatar } from "../camera/CameraRig";
import { ROLE_COLOR } from "../palette";

export function SelectionMarker() {
  const scene = useThree((s) => s.scene);
  const ring = useRef<Mesh>(null);
  const cache = useRef<{ id: string | null; object: Object3D | null }>({ id: null, object: null });
  const at = useMemo(() => new Vector3(), []);
  const color = useMemo(() => new Color(), []);

  useFrame(({ clock }) => {
    const mesh = ring.current;
    if (!mesh) return;
    const id = uiStore.getState().selectedAgentId;
    if (id !== cache.current.id) cache.current = { id, object: null };
    if (id) cache.current.object ??= findAvatar(scene, id);
    const object = cache.current.object;
    mesh.visible = Boolean(object);
    if (!object || !id) return;
    object.getWorldPosition(at);
    mesh.position.set(at.x, 0.02, at.z);
    const role = realtimeStore.getState().company?.agents[id]?.role ?? "spare";
    (mesh.material as MeshBasicMaterial).color.copy(color.set(ROLE_COLOR[role] ?? ROLE_COLOR.spare));
    const s = 1 + Math.sin(clock.elapsedTime * 3) * 0.05;
    mesh.scale.set(s, s, s);
  });

  return (
    <mesh ref={ring} rotation-x={-Math.PI / 2} visible={false} renderOrder={2}>
      <ringGeometry args={[0.55, 0.7, 40]} />
      <meshBasicMaterial transparent opacity={0.9} depthWrite={false} toneMapped={false} />
    </mesh>
  );
}

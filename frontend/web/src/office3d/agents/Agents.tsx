// Everyone in the office (T-405): one avatar per seated agent, with a name tag. React renders this
// only when the roster changes (who is here, their role, character, name) — the selector returns
// a string, so the thousand events of a busy day do not re-render it; each avatar reads its own
// state from the store in the frame loop.
import { Html, useGLTF } from "@react-three/drei";
import { Suspense, useMemo } from "react";

import { useRealtime } from "@/stores/realtime";

import { characterFor, characterUrl, type Character } from "../assets/characters";
import { ROLE_COLOR } from "../palette";
import { assignSeats, type Seat } from "../scene/layout";
import { AgentAvatar } from "./AgentAvatar";

interface Member {
  id: string;
  role: string;
  name: string;
  character: Character;
}

/** The roster as one string: changes only when someone joins, leaves or is renamed. */
export function rosterKey(agents: Record<string, { id: string; role: string; display_name: string; avatar_key: string }> | undefined): string {
  return Object.values(agents ?? {})
    .map((a) => [a.id, a.role, a.display_name, characterFor(a.id, a.avatar_key)].join("\t"))
    .sort()
    .join("\n");
}

function parseRoster(key: string): Member[] {
  if (!key) return [];
  return key.split("\n").map((line) => {
    const [id, role, name, character] = line.split("\t");
    return { id, role, name, character: character as Character };
  });
}

function LoadedAvatar({ member, seat }: { member: Member; seat: Seat }) {
  const gltf = useGLTF(characterUrl(member.character), false);
  const model = useMemo(() => ({ scene: gltf.scene, animations: gltf.animations }), [gltf]);
  return <AgentAvatar agentId={member.id} seat={seat} model={model} />;
}

function NameTag({ member, seat }: { member: Member; seat: Seat }) {
  return (
    <Html position={[seat.chair[0], 2.05, seat.chair[1]]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
      <span
        data-testid={`name-tag-${member.id}`}
        className="flex items-center gap-1 rounded-full bg-surface/90 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-ink shadow"
      >
        <span aria-hidden className="size-2 rounded-full" style={{ backgroundColor: ROLE_COLOR[member.role] ?? ROLE_COLOR.spare }} />
        {member.name}
      </span>
    </Html>
  );
}

export function Agents({ labels = true }: { labels?: boolean }) {
  const key = useRealtime((s) => rosterKey(s.company?.agents));
  const members = useMemo(() => parseRoster(key), [key]);
  const seats = useMemo(() => assignSeats(members).seats, [members]);
  return (
    <>
      {members.map((member) => {
        const seat = seats.get(member.id);
        if (!seat) return null; // no desk left: on the 2D board only
        return (
          <group key={member.id}>
            <Suspense fallback={null}>
              <LoadedAvatar member={member} seat={seat} />
            </Suspense>
            {labels ? <NameTag member={member} seat={seat} /> : null}
          </group>
        );
      })}
    </>
  );
}

// Everyone in the office (T-405): one avatar per seated agent. React renders this only when the
// roster changes (useRoster's selector is a string), so the thousand events of a busy day do not
// re-render it; each avatar reads its own state from the store in the frame loop. Name and status
// tags are HeadTags (T-406).
import { useGLTF } from "@react-three/drei";
import { Suspense, useMemo } from "react";

import { characterUrl } from "../assets/characters";
import type { Seat } from "../scene/layout";
import { AgentAvatar } from "./AgentAvatar";
import { useRoster, type Member } from "./roster";

export { rosterKey } from "./roster";

function LoadedAvatar({ member, seat }: { member: Member; seat: Seat }) {
  const gltf = useGLTF(characterUrl(member.character), false);
  const model = useMemo(() => ({ scene: gltf.scene, animations: gltf.animations }), [gltf]);
  return <AgentAvatar agentId={member.id} seat={seat} model={model} />;
}

export function Agents() {
  const { members, seats } = useRoster();
  return (
    <>
      {members.map((member) => {
        const seat = seats.get(member.id);
        if (!seat) return null; // no desk left: on the 2D board only
        return (
          <Suspense key={member.id} fallback={null}>
            <LoadedAvatar member={member} seat={seat} />
          </Suspense>
        );
      })}
    </>
  );
}

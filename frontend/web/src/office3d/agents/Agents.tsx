// Everyone in the office (T-405): one avatar per seated agent. React renders this only when the
// roster changes (useRoster's selector is a string), so the thousand events of a busy day do not
// re-render it; each avatar reads its own state from the store in the frame loop. Name and status
// tags are HeadTags (T-406).
//
// **Inside a department, only its people are drawn** (ARCHITECTURE_V2 §14.7, T-600): entering a
// room is meant to show that room's work, and eight avatars in the background are the thing the
// operator stepped in to get away from. The building stays — the desks and the floor of the rest
// of the company are still there — and the strip says how many people are elsewhere, so nobody
// silently disappears.
import { useGLTF } from "@react-three/drei";
import { Suspense, useMemo } from "react";

import { useUi, type EnteredDepartment } from "@/stores/ui";

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

/** Who is drawn: the entered room's people, or everybody when standing on the whole floor. */
export function membersInRoom(
  members: readonly Member[],
  entered: EnteredDepartment | null,
): Member[] {
  if (!entered) return [...members];
  return members.filter((m) => (m.department ?? m.office_zone_key) === entered.key);
}

export function Agents() {
  const { members, seats } = useRoster();
  const entered = useUi((s) => s.focusedDepartment);
  const here = membersInRoom(members, entered);
  return (
    <>
      {here.map((member) => {
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

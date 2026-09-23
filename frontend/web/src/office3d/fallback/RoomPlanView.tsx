// The room from above, drawn from the same seat layout the 3D office uses (T-410 stage 1).
//
// Plain shapes for now: desks are rectangles, the people at them are dots in their role's colour.
// The point of this stage is the arrangement — which room, how many desks, who is at which —
// with the drawing itself left until the layout is agreed.
"use client";

import { uiStore } from "@/stores/ui";

import type { BoardCard } from "./board";
import type { RoomPlan } from "./board";

const PERSON_R = 0.42;

export function RoomPlanView({
  plan,
  cards,
  selected,
  label,
}: {
  plan: RoomPlan;
  /** The people in these rooms, by id: a dot needs a colour and a name. */
  cards: Map<string, BoardCard>;
  selected: string | null;
  label: string;
}) {
  if (!plan.desks.length) return null;
  return (
    <svg
      role="img"
      aria-label={`${label}（俯視）`}
      data-testid="room-plan"
      viewBox={`0 0 ${plan.width} ${plan.height}`}
      className="h-56 w-full rounded-xl border border-line bg-canvas/40 sm:h-72"
      preserveAspectRatio="xMidYMid meet"
    >
      {plan.desks.map((desk) => {
        const card = desk.agentId ? cards.get(desk.agentId) : undefined;
        const isSelected = card !== undefined && card.id === selected;
        return (
          <g key={desk.key} data-testid={`plan-desk-${desk.key}`} data-agent={desk.agentId ?? undefined}>
            <rect
              x={desk.desk.left}
              y={desk.desk.top}
              width={desk.desk.width}
              height={desk.desk.height}
              rx={0.12}
              className="fill-surface stroke-line"
              strokeWidth={0.06}
            />
            {card ? (
              <g
                className="cursor-pointer"
                onClick={() => uiStore.getState().selectAgent(card.id)}
                data-testid={`plan-agent-${card.id}`}
              >
                <title>{`${card.name}・${card.visual.badge.text}`}</title>
                <circle
                  cx={desk.chair.cx}
                  cy={desk.chair.cy}
                  r={PERSON_R}
                  fill={card.color}
                  className={isSelected ? "stroke-accent" : "stroke-canvas"}
                  strokeWidth={isSelected ? 0.16 : 0.08}
                />
              </g>
            ) : (
              // an empty desk: the room is the room whether or not anybody is at it
              <circle cx={desk.chair.cx} cy={desk.chair.cy} r={desk.chair.r} className="fill-none stroke-line" strokeWidth={0.05} />
            )}
          </g>
        );
      })}
    </svg>
  );
}

// The room from above (T-410 stage 2), drawn from the same seat layout the 3D office uses.
//
// Pixel art made of rectangles: a tiled floor, a walkway down the middle, desks with two
// monitors, chairs, a plant in the corners, and a figure at every taken desk in its role's
// colour. Nothing is fetched — see ``console.ts`` for why — and the whole thing is one SVG, so
// it costs a browser without WebGL nothing to draw.
"use client";

import { uiStore } from "@/stores/ui";

import type { BoardCard, RoomPlan } from "./board";
import { CONSOLE } from "./console";

/** One drawing unit is one floor metre; a "pixel" is this much of it. */
const PX = 0.1;
const TILE = 1.2;

const px = (n: number) => Math.round(n / PX) * PX;

function Floor({ width, height }: { width: number; height: number }) {
  const tiles = [];
  for (let y = 0; y < height; y += TILE) {
    for (let x = 0; x < width; x += TILE) {
      const alt = (Math.round(x / TILE) + Math.round(y / TILE)) % 2 === 0;
      tiles.push(
        <rect key={`${x}-${y}`} x={x} y={y} width={TILE} height={TILE} fill={alt ? CONSOLE.floor : CONSOLE.floorAlt} />,
      );
    }
  }
  return <g aria-hidden>{tiles}</g>;
}

/** A desk seen from above: the slab, its lit top edge, and two monitors on it. */
function Desk({ x, y, width, height }: { x: number; y: number; width: number; height: number }) {
  const monitor = px(width * 0.3);
  const gap = px(width * 0.08);
  return (
    <g aria-hidden>
      <rect x={x} y={y} width={width} height={height} fill={CONSOLE.desk} />
      <rect x={x} y={y} width={width} height={px(height * 0.22)} fill={CONSOLE.deskTop} />
      {[-1, 1].map((side) => (
        <rect
          key={side}
          x={px(x + width / 2 + (side * gap) / 2 + (side < 0 ? -monitor : 0))}
          y={px(y + height * 0.14)}
          width={monitor}
          height={px(height * 0.44)}
          fill={CONSOLE.screen}
        />
      ))}
    </g>
  );
}

/** Whoever is at the desk: head, shoulders, and the role's colour. Four rectangles, no more. */
function Person({ cx, cy, color }: { cx: number; cy: number; color: string }) {
  const shoulders = px(0.7);
  const head = px(0.42);
  return (
    <g aria-hidden>
      {/* shoulders in the role's colour, a head above them, hair the same colour again */}
      <rect x={px(cx - shoulders / 2)} y={px(cy - 0.05)} width={shoulders} height={px(0.5)} fill={color} />
      <rect x={px(cx - head / 2)} y={px(cy - 0.42)} width={head} height={px(0.38)} fill={CONSOLE.text} />
      <rect x={px(cx - head / 2)} y={px(cy - 0.42)} width={head} height={px(0.14)} fill={color} />
    </g>
  );
}

function Plant({ x, y }: { x: number; y: number }) {
  return (
    <g aria-hidden>
      <rect x={x} y={y} width={px(0.5)} height={px(0.3)} fill={CONSOLE.chair} />
      <rect x={px(x + 0.08)} y={px(y - 0.45)} width={px(0.34)} height={px(0.45)} fill={CONSOLE.plant} />
      <rect x={px(x + 0.2)} y={px(y - 0.7)} width={px(0.1)} height={px(0.3)} fill={CONSOLE.plant} />
    </g>
  );
}

export function RoomPlanView({
  plan,
  cards,
  selected,
  label,
}: {
  plan: RoomPlan;
  /** The people in these rooms, by id: a figure needs a colour and a name. */
  cards: Map<string, BoardCard>;
  selected: string | null;
  label: string;
}) {
  if (!plan.desks.length) return null;
  const walkway = { y: px(plan.height / 2 - 0.55), height: px(1.1) };
  return (
    <svg
      role="img"
      aria-label={`${label}（俯視）`}
      data-testid="room-plan"
      viewBox={`0 0 ${plan.width} ${plan.height}`}
      className="h-56 w-full border-2 border-[color:var(--console-edge-dim)] sm:h-72"
      style={{ imageRendering: "pixelated", background: CONSOLE.floor }}
      preserveAspectRatio="xMidYMid meet"
      shapeRendering="crispEdges"
    >
      <Floor width={plan.width} height={plan.height} />
      {/* the corridor the couriers walk down, as the 3D floor has one */}
      <rect x={0} y={walkway.y} width={plan.width} height={walkway.height} fill={CONSOLE.walkway} />
      <Plant x={px(0.25)} y={px(plan.height - 0.4)} />
      <Plant x={px(plan.width - 0.75)} y={px(plan.height - 0.4)} />

      {plan.desks.map((desk) => {
        const card = desk.agentId ? cards.get(desk.agentId) : undefined;
        const isSelected = card !== undefined && card.id === selected;
        return (
          <g key={desk.key} data-testid={`plan-desk-${desk.key}`} data-agent={desk.agentId ?? undefined}>
            <Desk x={px(desk.desk.left)} y={px(desk.desk.top)} width={px(desk.desk.width)} height={px(desk.desk.height)} />
            {card ? (
              <g
                className="cursor-pointer"
                onClick={() => uiStore.getState().selectAgent(card.id)}
                data-testid={`plan-agent-${card.id}`}
              >
                <title>{`${card.name}・${card.visual.badge.text}`}</title>
                {isSelected ? (
                  <rect
                    x={px(desk.chair.cx - 0.55)}
                    y={px(desk.chair.cy - 0.6)}
                    width={px(1.1)}
                    height={px(1.25)}
                    fill="none"
                    stroke={CONSOLE.accent}
                    strokeWidth={PX}
                  />
                ) : null}
                <rect
                  x={px(desk.chair.cx - 0.42)}
                  y={px(desk.chair.cy - 0.1)}
                  width={px(0.84)}
                  height={px(0.66)}
                  fill={CONSOLE.chair}
                />
                <Person cx={desk.chair.cx} cy={desk.chair.cy} color={card.color} />
              </g>
            ) : (
              // an empty desk: the room is the room whether or not anybody is at it
              <rect
                x={px(desk.chair.cx - 0.25)}
                y={px(desk.chair.cy - 0.15)}
                width={px(0.5)}
                height={px(0.4)}
                fill="none"
                stroke={CONSOLE.edgeDim}
                strokeWidth={PX}
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}

"use client";

// The 2D office (T-410, 04 §8): the office without WebGL, and a view in its own right.
//
// Three columns on a desktop — who is here, the floor, and what just happened — over the same
// store and the same visual mapping as the 3D scene. The middle column is a room at a time: its
// desks from above, and a card per person. Clicking anywhere that names somebody selects them,
// as picking an avatar does; a hand-off flashes an arrow from one card to the next.
//
// On a phone only the middle column is drawn: this is also the fallback for a small screen, and
// three columns on a phone is none of them.
//
// It is the reference for the 3D layer too: whatever 3D cannot show, check here first whether
// the store has it.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useNow } from "@/hooks/useNow";
import { useRealtime, type RealtimeState } from "@/stores/realtime";
import { uiStore, useUi } from "@/stores/ui";

import { assignSeats } from "../scene/layout";
import type { BadgeTone } from "../visual/mapping";
import { arcBetween, boardModel, handoffsAfter, roomPlan, type BoardCard, type Box } from "./board";
import { LogColumn, RosterColumn } from "./BoardSideColumns";
import { RoomPlanView } from "./RoomPlanView";

/** The tab that shows every room at once; the floor rather than one room of it. */
export const ALL_ROOMS = "all";

export const HANDOFF_MS = 2500;

const TONE: Record<BadgeTone, string> = {
  muted: "bg-neutral/15 text-muted",
  info: "bg-accent/15 text-accent",
  active: "bg-ok/15 text-ok",
  warn: "bg-warn/20 text-warn",
  error: "bg-danger/15 text-danger",
  success: "bg-ok/15 text-ok",
};

interface Arrow {
  key: string;
  from: string;
  to: string;
}

/** Arrows for hand-offs that arrive while the board is shown (not for old ones at mount). */
function useHandoffArrows(company: RealtimeState | null): Arrow[] {
  const [arrows, setArrows] = useState<Arrow[]>([]);
  const seen = useRef<{ companyId: string | null; seq: number }>({ companyId: null, seq: 0 });
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    if (!company) return;
    if (seen.current.companyId !== company.companyId) {
      seen.current = { companyId: company.companyId, seq: company.lastSeq };
      return;
    }
    const fresh = handoffsAfter(company.recentEvents, seen.current.seq, company.agents);
    seen.current.seq = company.lastSeq;
    if (!fresh.length) return;
    const added = fresh.flatMap((h) => h.to.map((to) => ({ key: `${h.seq}:${to}`, from: h.from, to })));
    setArrows((current) => [...current, ...added]);
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      setArrows((current) => current.filter((a) => !added.includes(a)));
    }, HANDOFF_MS);
    timers.current.add(timer);
  }, [company]);

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);
  return arrows;
}

function Card({ card, selected, cardRef }: { card: BoardCard; selected: boolean; cardRef: (el: HTMLElement | null) => void }) {
  const { visual } = card;
  const blinking = visual.deskLight === "blink_amber" || visual.deskLight === "blink_red";
  return (
    <button
      ref={cardRef}
      type="button"
      data-testid={`board-agent-${card.id}`}
      data-pose={visual.pose}
      aria-pressed={selected}
      onClick={() => uiStore.getState().selectAgent(card.id)}
      className={`relative grid gap-1.5 rounded-xl border bg-surface p-3 pl-4 text-left shadow-sm transition-colors hover:border-accent ${
        selected ? "border-accent ring-2 ring-accent/40" : "border-line"
      } ${blinking ? "animate-pulse" : ""}`}
    >
      <span aria-hidden className="absolute inset-y-2 left-1.5 w-1 rounded-full" style={{ backgroundColor: card.color }} />
      <span className="flex items-center justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate font-semibold">{card.name}</span>
          <span className="block text-xs text-muted">{card.roleLabel}</span>
        </span>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${TONE[visual.badge.tone]}`}>{visual.badge.text}</span>
      </span>
      {visual.bubble ? <span className="truncate text-sm italic text-muted">「{visual.bubble}」</span> : null}
      <span className="truncate text-sm">{card.taskName ?? <span className="text-muted">沒有進行中的任務</span>}</span>
      <span className="flex justify-between gap-2 text-xs text-muted">
        <span className="truncate">
          {card.lastDone ? `最近完成：${card.lastDone.taskName ?? card.lastDone.summary ?? "—"}` : " "}
        </span>
        <span className="shrink-0 tabular-nums">{card.since}</span>
      </span>
    </button>
  );
}

export function OfficeBoard2D({
  departmentNames = {},
}: {
  /** key -> name, from the org chart; without it a room shows the key it is known by. */
  departmentNames?: Readonly<Record<string, string>>;
}) {
  const company = useRealtime((s) => s.company);
  const selected = useUi((s) => s.selectedAgentId);
  const now = useNow();
  const rows = boardModel(company, now, departmentNames);
  const arrows = useHandoffArrows(company);
  const [room, setRoom] = useState<string>(ALL_ROOMS);

  // a room that empties out (its last agent left, or the company changed) is not a room to stand in
  const shown = rows.some((row) => row.id === room) ? room : ALL_ROOMS;
  const visible = shown === ALL_ROOMS ? rows : rows.filter((row) => row.id === shown);
  const cards = visible.flatMap((row) => row.cards);
  const byId = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards]);
  const plan = useMemo(() => {
    const agents = Object.values(company?.agents ?? {});
    return roomPlan(
      cards.map((card) => card.id),
      assignSeats(agents).seats,
    );
  }, [company?.agents, cards]);

  const container = useRef<HTMLDivElement>(null);
  const cardRefs = useRef(new Map<string, HTMLElement>());
  const [lines, setLines] = useState<(Arrow & { d: string })[]>([]);
  useLayoutEffect(() => {
    const box = container.current?.getBoundingClientRect();
    if (!box) return;
    const rect = (id: string): Box | null => {
      const r = cardRefs.current.get(id)?.getBoundingClientRect();
      return r ? { left: r.left - box.left, top: r.top - box.top, width: r.width, height: r.height } : null;
    };
    setLines(
      arrows.flatMap((a) => {
        const from = rect(a.from);
        const to = rect(a.to);
        return from && to ? [{ ...a, d: arcBetween(from, to).d }] : [];
      }),
    );
  }, [arrows, shown]);

  if (!rows.length) return <p className="p-4 text-sm text-muted">這間公司還沒有代理。</p>;
  return (
    <div
      aria-label="辦公室（2D）"
      className="grid h-full min-h-0 lg:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_minmax(0,18rem)]"
    >
      <RosterColumn cards={rows.flatMap((row) => row.cards)} selected={selected} />

      <section aria-label="樓層" className="min-h-0 min-w-0 overflow-y-auto">
        <div role="tablist" aria-label="房間" className="flex flex-wrap gap-1 border-b border-line px-3 py-2 text-xs">
          {[{ id: ALL_ROOMS, label: "全部", businessColor: null }, ...rows].map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={shown === tab.id}
              data-testid={`room-tab-${tab.id}`}
              onClick={() => setRoom(tab.id)}
              className={`flex items-center gap-1.5 rounded-md px-2 py-1 ${
                shown === tab.id ? "bg-accent text-canvas" : "text-muted hover:text-fg"
              }`}
            >
              {tab.businessColor ? (
                <span aria-hidden className="size-1.5 rounded-full" style={{ backgroundColor: tab.businessColor }} />
              ) : null}
              {tab.label}
            </button>
          ))}
        </div>

        <div ref={container} className="relative grid gap-6 p-3 pt-6">
          <RoomPlanView
            plan={plan}
            cards={byId}
            selected={selected}
            label={shown === ALL_ROOMS ? "全部" : (rows.find((row) => row.id === shown)?.label ?? shown)}
          />
          {visible.map((row) => (
            <section key={row.id} aria-label={row.label}>
              <h3 className="mb-2 flex items-center gap-2 text-xs font-medium tracking-wide text-muted">
                {/* the same colour the 3D floor marks this room with; none for a shared function */}
                {row.businessColor ? (
                  <span
                    aria-hidden
                    data-testid={`row-business-${row.id}`}
                    title={row.business ?? undefined}
                    className="size-2 rounded-full"
                    style={{ backgroundColor: row.businessColor }}
                  />
                ) : null}
                {row.label}
              </h3>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
                {row.cards.map((card) => (
                  <Card
                    key={card.id}
                    card={card}
                    selected={selected === card.id}
                    cardRef={(el) => {
                      if (el) cardRefs.current.set(card.id, el);
                      else cardRefs.current.delete(card.id);
                    }}
                  />
                ))}
              </div>
            </section>
          ))}
          {arrows.map((a) => (
            <span key={a.key} hidden data-testid="handoff-arrow" data-from={a.from} data-to={a.to} />
          ))}
          <svg aria-hidden className="pointer-events-none absolute inset-0 h-full w-full overflow-visible">
            <defs>
              <marker id="handoff-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" className="fill-accent" />
              </marker>
            </defs>
            {lines.map((l) => (
              <path
                key={l.key}
                d={l.d}
                fill="none"
                className="animate-pulse stroke-accent"
                strokeWidth={3}
                strokeDasharray="8 6"
                markerEnd="url(#handoff-head)"
              />
            ))}
          </svg>
        </div>
      </section>

      <LogColumn events={company?.recentEvents ?? []} />
    </div>
  );
}

"use client";

// The 2D board (T-410, 04 §8): the office without WebGL. Same store, same visual mapping as the
// 3D scene, cards in floor-plan rows; clicking a card selects the agent (like picking an avatar);
// a hand-off flashes an arrow from one card to the next for a moment. It is also the reference
// for the 3D layer: whatever 3D cannot show, check here first whether the store has it.
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useNow } from "@/hooks/useNow";
import { useRealtime, type RealtimeState } from "@/stores/realtime";
import { uiStore, useUi } from "@/stores/ui";

import type { BadgeTone } from "../visual/mapping";
import { arcBetween, boardModel, handoffsAfter, type BoardCard, type Box } from "./board";

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

export function OfficeBoard2D() {
  const company = useRealtime((s) => s.company);
  const selected = useUi((s) => s.selectedAgentId);
  const now = useNow();
  const rows = boardModel(company, now);
  const arrows = useHandoffArrows(company);

  const container = useRef<HTMLDivElement>(null);
  const cards = useRef(new Map<string, HTMLElement>());
  const [lines, setLines] = useState<(Arrow & { d: string })[]>([]);
  useLayoutEffect(() => {
    const box = container.current?.getBoundingClientRect();
    if (!box) return;
    const rect = (id: string): Box | null => {
      const r = cards.current.get(id)?.getBoundingClientRect();
      return r ? { left: r.left - box.left, top: r.top - box.top, width: r.width, height: r.height } : null;
    };
    setLines(
      arrows.flatMap((a) => {
        const from = rect(a.from);
        const to = rect(a.to);
        return from && to ? [{ ...a, d: arcBetween(from, to).d }] : [];
      }),
    );
  }, [arrows]);

  if (!rows.length) return <p className="p-4 text-sm text-muted">這間公司還沒有代理。</p>;
  return (
    <div ref={container} className="relative grid gap-8 p-4 pt-10" aria-label="辦公室（2D）">
      {rows.map((row) => (
        <section key={row.id} aria-label={row.label}>
          <h3 className="mb-2 text-xs font-medium tracking-wide text-muted">{row.label}</h3>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-3">
            {row.cards.map((card) => (
              <Card
                key={card.id}
                card={card}
                selected={selected === card.id}
                cardRef={(el) => {
                  if (el) cards.current.set(card.id, el);
                  else cards.current.delete(card.id);
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
  );
}

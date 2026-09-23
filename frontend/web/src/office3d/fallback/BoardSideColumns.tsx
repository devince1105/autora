// The two side columns of the 2D office (T-410 stage 1): who is here, and what just happened.
//
// They are the same store the middle column reads — no new data, a second way to look at it.
// On a narrow screen they are not drawn at all: the board is the fallback for a phone, and
// three columns on a phone is none of them.
"use client";

import { describeEvent } from "@/events/describe";
import type { EventEnvelope } from "@autora/event-schema";

import { uiStore } from "@/stores/ui";

import type { BoardCard } from "./board";
import { BADGE_INK, CONSOLE, TONE_INK } from "./console";

const TIME = new Intl.DateTimeFormat("zh-TW", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "Asia/Taipei",
});

export function RosterColumn({ cards, selected }: { cards: BoardCard[]; selected: string | null }) {
  return (
    <aside
      aria-label="人員"
      data-testid="board-roster"
      className="hidden min-h-0 overflow-y-auto border-r-2 border-[color:var(--console-edge-dim)] lg:block"
    >
      <h2 className="sticky top-0 bg-[color:var(--console-panel-dim)] px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-[color:var(--console-text-dim)]">
        人員 <span className="tabular-nums">{cards.length}</span>
      </h2>
      <ul className="grid">
        {cards.map((card) => (
          <li key={card.id}>
            <button
              type="button"
              data-testid={`roster-${card.id}`}
              aria-pressed={selected === card.id}
              onClick={() => uiStore.getState().selectAgent(card.id)}
              className={`grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2 border-l-4 px-3 py-2 text-left hover:bg-[color:var(--console-panel)] ${
                selected === card.id
                  ? "border-l-[color:var(--console-accent)] bg-[color:var(--console-panel)]"
                  : "border-l-transparent"
              }`}
            >
              {/* a square, not a dot: everything on this panel is made of pixels */}
              <span aria-hidden className="size-2" style={{ backgroundColor: card.color }} />
              <span className="min-w-0">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium text-[color:var(--console-text)]">{card.name}</span>
                  <span className="shrink-0 text-[11px] tabular-nums text-[color:var(--console-text-dim)]">{card.since}</span>
                </span>
                <span className="flex items-baseline justify-between gap-2 text-[11px] text-[color:var(--console-text-dim)]">
                  <span className="truncate">{card.roleLabel}</span>
                  <span className="shrink-0" style={{ color: BADGE_INK[card.visual.badge.tone] ?? CONSOLE.textDim }}>
                    {card.visual.badge.text}
                  </span>
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

export function LogColumn({ events }: { events: readonly EventEnvelope[] }) {
  const lines = [...events].reverse().slice(0, 60);
  return (
    <aside
      aria-label="即時紀錄"
      data-testid="board-log"
      className="hidden min-h-0 overflow-y-auto border-l-2 border-[color:var(--console-edge-dim)] lg:block"
    >
      <h2 className="sticky top-0 bg-[color:var(--console-panel-dim)] px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-[color:var(--console-text-dim)]">
        即時紀錄
      </h2>
      <ol className="grid gap-1 px-3 pb-3">
        {lines.map((event) => {
          const described = describeEvent(event.event_type, event.payload);
          return (
            <li key={event.event_id} className="grid grid-cols-[auto_auto_minmax(0,1fr)] items-baseline gap-2 text-[11px]">
              <time className="tabular-nums text-[color:var(--console-text-dim)]" dateTime={event.occurred_at}>
                {TIME.format(new Date(event.occurred_at))}
              </time>
              <span aria-hidden className="size-1.5" style={{ backgroundColor: TONE_INK[described.tone] ?? CONSOLE.textDim }} />
              <span className="min-w-0 truncate" title={described.summary ?? described.label}>
                <span className="font-medium" style={{ color: TONE_INK[described.tone] ?? CONSOLE.text }}>
                  {described.label}
                </span>
                {described.summary ? <span className="text-[color:var(--console-text-dim)]">・{described.summary}</span> : null}
              </span>
            </li>
          );
        })}
        {lines.length === 0 ? (
          <li className="text-[11px] text-[color:var(--console-text-dim)]">還沒有事件。</li>
        ) : null}
      </ol>
    </aside>
  );
}

import Link from "next/link";

import { TONE_DOT } from "@/features/events/describe";
import { RECENT_EVENTS_KEPT } from "@/realtime/reducer";
import type { TimelineFilters } from "@/stores/ui";

import type { TimelineItem } from "./model";

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("zh-TW", { hour12: false });
}

function Chip({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs ${
        pressed ? "border-accent bg-accent/15 text-ink" : "border-line text-muted hover:border-accent"
      }`}
    >
      {children}
    </button>
  );
}

export interface TimelineViewProps {
  items: TimelineItem[];
  buffered: number;
  options: { agents: { id: string; name: string }[]; types: string[] };
  filters: TimelineFilters;
  paused: boolean;
  newWhilePaused: number;
  onPause: (paused: boolean) => void;
  onToggleAgent: (id: string) => void;
  onToggleType: (type: string) => void;
  onClearFilters: () => void;
}

export function TimelineView(props: TimelineViewProps) {
  const { items, options, filters, paused } = props;
  const filtered = filters.agentIds.length > 0 || filters.eventTypes.length > 0;
  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          aria-pressed={paused}
          onClick={() => props.onPause(!paused)}
          className="rounded-lg border border-line bg-surface px-4 py-1.5 text-sm hover:border-accent"
        >
          {paused ? "繼續" : "暫停"}
        </button>
        <p className="text-sm text-muted" role="status">
          {paused
            ? `已暫停・${props.newWhilePaused ? `有 ${props.newWhilePaused} 則新事件` : "沒有新事件"}`
            : "即時更新中"}
          ・顯示 {items.length} 則（緩衝 {props.buffered} / 最多 {RECENT_EVENTS_KEPT}）
        </p>
      </div>

      <fieldset className="mb-3">
        <legend className="mb-2 text-xs font-medium text-muted">代理</legend>
        <div className="flex flex-wrap gap-2">
          {options.agents.map((agent) => (
            <Chip key={agent.id} pressed={filters.agentIds.includes(agent.id)} onClick={() => props.onToggleAgent(agent.id)}>
              {agent.name}
            </Chip>
          ))}
        </div>
      </fieldset>
      <fieldset className="mb-4">
        <legend className="mb-2 text-xs font-medium text-muted">事件類型</legend>
        <div className="flex flex-wrap gap-2">
          {options.types.map((type) => (
            <Chip key={type} pressed={filters.eventTypes.includes(type)} onClick={() => props.onToggleType(type)}>
              {type}
            </Chip>
          ))}
        </div>
      </fieldset>
      {filtered ? (
        <button type="button" onClick={props.onClearFilters} className="mb-4 text-sm text-accent underline">
          清除篩選
        </button>
      ) : null}

      {items.length ? (
        <ol className="grid gap-1" aria-label="事件">
          {items.map((item) => (
            <li
              key={item.key}
              data-testid={`event-${item.seq}`}
              className="rounded-lg border border-line bg-surface px-3 py-2"
            >
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span aria-hidden className={`size-2 shrink-0 self-center rounded-full ${TONE_DOT[item.tone]}`} />
                <span className="text-xs text-muted tabular-nums">{time(item.at)}</span>
                <span className="text-xs text-muted tabular-nums">#{item.seq}</span>
                <span className="text-sm">{item.actor}</span>
                <span className="font-medium">{item.label}</span>
                {item.summary ? <span className="min-w-0 break-words text-sm text-muted">{item.summary}</span> : null}
                <span className="font-mono text-xs text-muted">{item.eventType}</span>
                {item.runId ? (
                  <Link href={`/trace/${item.runId}`} className="text-xs text-accent underline">
                    軌跡
                  </Link>
                ) : null}
                {item.taskId ? (
                  <Link href={`/tasks/${item.taskId}`} className="text-xs text-accent underline">
                    任務
                  </Link>
                ) : null}
              </div>
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer text-muted">原始資料</summary>
                <pre className="mt-1 overflow-x-auto rounded border border-line bg-canvas p-2">
                  {JSON.stringify(item.payload, null, 2)}
                </pre>
              </details>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-muted">{filtered ? "沒有符合篩選的事件。" : "還沒有事件。"}</p>
      )}
    </>
  );
}

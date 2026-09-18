import { formatDuration, ROLE_LABEL, type CardModel } from "./model";
import { ProgressBar, StateBadge } from "./StateBadge";

export function AgentCards({
  cards,
  selectedId,
  onSelect,
}: {
  cards: CardModel[];
  selectedId: string | null;
  onSelect: (agentId: string) => void;
}) {
  if (!cards.length) {
    return <p className="text-sm text-muted">這間公司還沒有代理。</p>;
  }
  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3" aria-label="代理">
      {cards.map((card) => (
        <li key={card.id}>
          <button
            type="button"
            onClick={() => onSelect(card.id)}
            aria-pressed={card.id === selectedId}
            data-testid={`agent-card-${card.id}`}
            className={`grid w-full gap-2 rounded-xl border bg-surface p-4 text-left transition-colors hover:border-accent ${
              card.id === selectedId ? "border-accent" : "border-line"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate font-semibold">{card.name}</p>
                <p className="text-xs text-muted">{ROLE_LABEL[card.role] ?? card.role}</p>
              </div>
              <StateBadge state={card.state} label={card.stateLabel} />
            </div>
            <p className="truncate text-sm">
              {card.taskName ?? <span className="text-muted">沒有進行中的任務</span>}
            </p>
            <p className="flex justify-between text-xs text-muted">
              <span>{card.tool ? `工具：${card.tool}` : " "}</span>
              <span className="tabular-nums">{formatDuration(card.sinceMs)}</span>
            </p>
            {card.progress ? <ProgressBar {...card.progress} /> : null}
          </button>
        </li>
      ))}
    </ul>
  );
}

import type { ActivityState } from "@/realtime/snapshot";

const TONE: Record<ActivityState, string> = {
  IDLE: "bg-neutral/15 text-muted",
  THINKING: "bg-accent/15 text-accent",
  WORKING: "bg-ok/15 text-ok",
  WAITING: "bg-warn/15 text-warn",
  REVIEWING: "bg-accent/15 text-accent",
  COMPLETED: "bg-ok/15 text-ok",
  FAILED: "bg-danger/15 text-danger",
  PAUSED: "bg-neutral/15 text-muted",
};

export function StateBadge({ state, label }: { state: ActivityState; label: string }) {
  return (
    <span
      data-state={state}
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${TONE[state]}`}
    >
      {label}
    </span>
  );
}

export function ProgressBar({ label, current, target }: { label: string; current: number; target: number | null }) {
  const percent = target ? Math.min(100, Math.round((current / target) * 100)) : null;
  return (
    <div className="grid gap-1" data-testid="progress">
      <div className="flex justify-between text-xs text-muted">
        <span>{label}</span>
        <span className="tabular-nums">
          {current}
          {target !== null ? ` / ${target}` : null}
        </span>
      </div>
      {percent !== null ? (
        <div className="h-1.5 overflow-hidden rounded-full bg-line">
          <div className="h-full rounded-full bg-accent" style={{ width: `${percent}%` }} />
        </div>
      ) : null}
    </div>
  );
}

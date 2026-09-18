"use client";

// The 2D board (04 §8): the office without WebGL. T-401 shows who is there and in which state;
// T-410 turns it into the full board (role colours, tasks, handoff arrows) using visual/mapping.
import { useRealtime } from "@/stores/realtime";

export function OfficeBoard2D() {
  const agents = useRealtime((state) => state.company?.agents);
  const list = Object.values(agents ?? {}).sort((a, b) => a.display_name.localeCompare(b.display_name));
  if (!list.length) return <p className="p-4 text-sm text-muted">這間公司還沒有代理。</p>;
  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] gap-3 p-4" aria-label="辦公室（2D）">
      {list.map((agent) => (
        <li key={agent.id} className="rounded-xl border border-line bg-surface p-3" data-testid={`board-agent-${agent.id}`}>
          <p className="font-semibold">{agent.display_name}</p>
          <p className="text-xs text-muted">{agent.role}</p>
          <p className="mt-2 text-sm" data-state={agent.activity?.state ?? "UNKNOWN"}>
            {agent.activity?.state ?? "—"}
          </p>
        </li>
      ))}
    </ul>
  );
}

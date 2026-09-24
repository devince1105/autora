"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { runQuery, traceQuery } from "@/api/queries";
import { useNow } from "@/hooks/useNow";
import { useRealtime } from "@/stores/realtime";
import { useUi } from "@/stores/ui";

import { AgentCards } from "./AgentCards";
import { AgentPanelView } from "./AgentPanelView";
import { cardModel, nextSteps, panelLinks, producedCounts, toolStats, type CardModel } from "./model";

/** The company's agents as cards; clicking one selects it (ui store) and opens its panel. */
export function AgentList() {
  const company = useRealtime((state) => state.company);
  const selectedId = useUi((state) => state.selectedAgentId);
  const selectAgent = useUi((state) => state.selectAgent);
  const now = useNow();
  const cards = Object.values(company?.agents ?? {})
    .map((agent) => cardModel(agent, now))
    .filter((card): card is CardModel => card !== null);
  return <AgentCards cards={cards} selectedId={selectedId} onSelect={selectAgent} />;
}

/** The selected agent's panel: live layer from the store, detail layer from REST (run, trace). */
export function AgentPanel() {
  const selectedId = useUi((state) => state.selectedAgentId);
  const tab = useUi((state) => state.panelTab);
  const setTab = useUi((state) => state.setPanelTab);
  const selectAgent = useUi((state) => state.selectAgent);
  const company = useRealtime((state) => state.company);
  const now = useNow();

  const agent = selectedId ? company?.agents[selectedId] : undefined;
  const runId = agent?.activity?.run_id ?? null;
  const run = useQuery({ ...runQuery(runId ?? ""), enabled: Boolean(runId) });
  const trace = useQuery({ ...traceQuery(runId ?? ""), enabled: Boolean(runId) });

  useEffect(() => {
    if (!selectedId) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") selectAgent(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, selectAgent]);

  if (!agent || !company) return null;
  const card = cardModel(agent, now);
  if (!card) return null;
  const links = panelLinks(agent.activity?.detail.links, company.companyId);
  const error = run.error ?? trace.error;

  return (
    <AgentPanelView
      data={{
        card,
        runId,
        nextSteps: nextSteps(agent, company),
        links,
        run: run.data,
        trace: trace.data,
        produced: producedCounts(trace.data),
        tools: toolStats(trace.data),
        loading: run.isFetching || trace.isFetching,
        error: error ? error.message : null,
      }}
      tab={tab}
      onTab={setTab}
      onClose={() => selectAgent(null)}
    />
  );
}

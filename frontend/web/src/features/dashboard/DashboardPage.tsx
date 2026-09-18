"use client";

import { useQuery } from "@tanstack/react-query";

import { approvalsQuery, kpisQuery } from "@/api/queries";
import { AgentList, AgentPanel } from "@/features/agent-panel/AgentPanel";
import { CompanyScope, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";
import { useNow } from "@/hooks/useNow";
import { useRealtime } from "@/stores/realtime";

import { DashboardView } from "./DashboardView";
import { dashboardModel } from "./model";

/** The dashboard of one company: ?company=<id>, or the first company. */
export function DashboardPage() {
  return <CompanyScope>{(company) => <CompanyDashboard company={company} />}</CompanyScope>;
}

function CompanyDashboard({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const kpis = useQuery(kpisQuery(company.id));
  const pending = useQuery(approvalsQuery(company.id));
  const realtime = useRealtime((state) => state.company);
  const connection = useRealtime((state) => state.connection);
  const now = useNow();

  const model = dashboardModel(
    realtime?.companyId === company.id ? realtime : null,
    kpis.data,
    connection,
    now,
  );
  return (
    <>
      <DashboardView
        companyId={company.id}
        companyName={company.name}
        model={model}
        pendingApprovals={pending.data?.length ?? null}
      />
      <section className="mx-auto max-w-6xl px-4 pb-12" aria-labelledby="agents-heading">
        <h2 id="agents-heading" className="mb-3 text-lg font-semibold">
          代理
        </h2>
        <AgentList />
      </section>
      <AgentPanel />
    </>
  );
}

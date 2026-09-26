"use client";

import Link from "next/link";

import { CompanyScope, withCompany, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";
import { ConnectionBadge } from "@/features/dashboard/DashboardView";
import { connectionModel } from "@/features/dashboard/model";
import { useNow } from "@/hooks/useNow";
import type { AgentState } from "@/realtime/reducer";
import { useRealtime } from "@/stores/realtime";

import { Timeline } from "./Timeline";

const NO_EVENTS: never[] = [];
const NO_AGENTS: Record<string, AgentState> = {};

/** /timeline: the company's recent events as they arrive. */
export function TimelinePage() {
  return <CompanyScope>{(company) => <CompanyTimeline company={company} />}</CompanyScope>;
}

function CompanyTimeline({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const current = useRealtime((state) => (state.company?.companyId === company.id ? state.company : null));
  const connection = useRealtime((state) => state.connection);
  const now = useNow();
  return (
    <main className="mx-auto max-w-5xl px-4 pt-8 pb-12">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs tracking-widest text-muted uppercase">Timeline</p>
          <h1 className="mt-1 text-2xl font-semibold">{company.name} 的事件</h1>
        </div>
        <div className="flex items-center gap-4">
          <Link href={withCompany("/admin/dashboard", company.id)} className="text-sm text-accent underline">
            Dashboard
          </Link>
          <ConnectionBadge connection={connectionModel(connection, current !== null, now)} />
        </div>
      </header>
      <Timeline
        companyId={company.id}
        events={current?.recentEvents ?? NO_EVENTS}
        agents={current?.agents ?? NO_AGENTS}
      />
    </main>
  );
}

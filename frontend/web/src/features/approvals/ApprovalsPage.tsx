"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import {
  approvalsQuery,
  decideApproval,
  failedWorkflowsQuery,
  queryKeys,
  restartWorkflow,
} from "@/api/queries";
import { CompanyScope, withCompany, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";
import { ConnectionBadge } from "@/features/dashboard/DashboardView";
import { connectionModel } from "@/features/dashboard/model";
import { useNow } from "@/hooks/useNow";
import type { AgentState } from "@/realtime/reducer";
import { useRealtime } from "@/stores/realtime";

import { ApprovalInbox } from "./ApprovalInbox";
import { FailedRuns } from "./FailedRuns";
import { approvalCard, type ApprovalState } from "./model";

const NO_AGENTS: Record<string, AgentState> = {};

/** /approvals: what the runtime is waiting for a human to decide. */
export function ApprovalsPage() {
  return <CompanyScope>{(company) => <CompanyApprovals company={company} />}</CompanyScope>;
}

function CompanyApprovals({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const [state, setState] = useState<ApprovalState>("PENDING");
  const approvals = useQuery(approvalsQuery(company.id, state));
  const failed = useQuery(failedWorkflowsQuery(company.id));
  const queryClient = useQueryClient();
  const current = useRealtime((s) => (s.company?.companyId === company.id ? s.company : null));
  const connection = useRealtime((s) => s.connection);
  const now = useNow();
  const agents = current?.agents ?? NO_AGENTS;

  return (
    <main className="mx-auto max-w-4xl px-4 pt-8 pb-12">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs tracking-widest text-muted uppercase">Approvals</p>
          <h1 className="mt-1 text-2xl font-semibold">{company.name} 的審批收件匣</h1>
        </div>
        <div className="flex items-center gap-4">
          <Link href={withCompany("/dashboard", company.id)} className="text-sm text-accent underline">
            Dashboard
          </Link>
          <ConnectionBadge connection={connectionModel(connection, current !== null, now)} />
        </div>
      </header>
      <ApprovalInbox
        state={state}
        onState={setState}
        cards={approvals.data?.map((a) => approvalCard(a, agents, now))}
        loadError={approvals.error?.message ?? null}
        decide={(id, decision, reason) => decideApproval(id, decision, reason)}
        live={connection.status === "live"}
        refresh={() => queryClient.invalidateQueries({ queryKey: ["approvals", company.id] })}
      />
      {/* the other thing the inbox is for: work that failed and could be run again (AC-9) */}
      <FailedRuns
        runs={failed.data}
        onRestart={async (runId) => {
          const result = await restartWorkflow(company.id, runId);
          await queryClient.invalidateQueries({ queryKey: queryKeys.failedWorkflows(company.id) });
          return result;
        }}
      />
    </main>
  );
}


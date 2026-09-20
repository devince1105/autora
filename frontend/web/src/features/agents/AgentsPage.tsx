"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import { agentsQuery, hireAgent, rolesQuery } from "@/api/queries";
import { CompanyScope, withCompany, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";

import { AgentsView, HireForm } from "./AgentsView";

/** /agents: who works at this company, and hiring one more. */
export function AgentsPage() {
  return <CompanyScope>{(company) => <CompanyAgents company={company} />}</CompanyScope>;
}

function CompanyAgents({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const agents = useQuery(agentsQuery(company.id));
  const roles = useQuery(rolesQuery());
  const queryClient = useQueryClient();

  return (
    <main className="mx-auto max-w-3xl px-4 pt-8 pb-12">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs tracking-widest text-muted uppercase">Agents</p>
          <h1 className="mt-1 text-2xl font-semibold">{company.name} 的代理</h1>
        </div>
        <div className="flex gap-4 text-sm">
          <Link href={withCompany("/dashboard", company.id)} className="text-accent underline">
            Dashboard
          </Link>
          <Link href={withCompany("/office", company.id)} className="text-accent underline">
            辦公室
          </Link>
        </div>
      </header>
      {agents.error ? <p className="mb-3 text-danger">{agents.error.message}</p> : null}
      <AgentsView agents={agents.data} />
      <h2 className="mt-8 mb-3 text-lg font-semibold">雇用代理</h2>
      <HireForm
        roles={roles.data?.roles}
        taken={(agents.data ?? []).map((a) => a.role)}
        onHire={async (agent) => {
          await hireAgent(company.id, agent);
          await queryClient.invalidateQueries({ queryKey: ["agents", company.id] });
        }}
      />
    </main>
  );
}

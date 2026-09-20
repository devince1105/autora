"use client";

import { useSuspenseQuery } from "@tanstack/react-query";

import { cyclesQuery } from "@/api/queries";
import { CompanyScope, type Company } from "@/features/company/CompanyScope";

import { CyclesView } from "./CyclesView";

/** The company's days: ?company=<id>, or the first company. */
export function CyclesPage() {
  return (
    <CompanyScope>
      {(company) => <CompanyCycles company={company} />}
    </CompanyScope>
  );
}

function CompanyCycles({ company }: { company: Company }) {
  const { data } = useSuspenseQuery(cyclesQuery(company.id));
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
      <h1 className="text-2xl font-semibold">營運週期</h1>
      <CyclesView cycles={data} />
    </main>
  );
}

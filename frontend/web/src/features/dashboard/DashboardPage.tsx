"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { ApiError } from "@/api/client";
import { companiesQuery, kpisQuery } from "@/api/queries";
import { AgentList, AgentPanel } from "@/features/agent-panel/AgentPanel";
import { storeToken } from "@/features/auth/TokenGate";
import { useCompanyStream } from "@/features/company/useCompanyStream";
import { useNow } from "@/hooks/useNow";
import { useRealtime } from "@/stores/realtime";

import { DashboardView } from "./DashboardView";
import { dashboardModel } from "./model";

/** The dashboard of one company: ?company=<id>, or the first company. */
export function DashboardPage() {
  const requested = useSearchParams().get("company");
  const companies = useQuery(companiesQuery());
  const company =
    companies.data?.find((c) => c.id === requested) ?? (requested ? undefined : companies.data?.[0]);

  useCompanyStream(company?.id ?? null);
  const kpis = useQuery({ ...kpisQuery(company?.id ?? ""), enabled: Boolean(company) });
  const realtime = useRealtime((state) => state.company);
  const connection = useRealtime((state) => state.connection);
  const now = useNow();

  const unauthorized = companies.error instanceof ApiError && companies.error.status === 401;
  useEffect(() => {
    if (unauthorized) storeToken(null); // back to the token form
  }, [unauthorized]);
  if (companies.isPending) return <p className="mx-auto max-w-6xl px-4 pt-8 text-muted">載入中…</p>;
  if (companies.error) {
    return (
      <p className="mx-auto max-w-6xl px-4 pt-8" role="alert">
        無法載入公司列表：{companies.error.message}
      </p>
    );
  }
  if (!company) {
    return (
      <p className="mx-auto max-w-6xl px-4 pt-8" role="alert">
        {requested ? `找不到公司 ${requested}` : "還沒有任何公司。先執行 backend/scripts/seed_echo.py 建立示範公司。"}
      </p>
    );
  }

  const model = dashboardModel(
    realtime?.companyId === company.id ? realtime : null,
    kpis.data,
    connection,
    now,
  );
  return (
    <>
      <DashboardView companyName={company.name} model={model} />
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

"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { ApiError, type Schemas } from "@/api/client";
import { companiesQuery } from "@/api/queries";
import { storeToken } from "@/features/auth/TokenGate";

export type Company = Schemas["CompanyOut"];

const message = (text: string, alert = false) => (
  <p className="mx-auto max-w-6xl px-4 pt-8 text-muted" role={alert ? "alert" : undefined}>
    {text}
  </p>
);

/**
 * The company an admin page shows: ?company=<id>, or — with none asked for — the first company
 * that has agents (a company nobody works at shows an empty office; old test companies linger in
 * a developer's database). Renders loading, error and "no company" states itself; a 401 goes back
 * to the token form.
 */
export function CompanyScope({ children }: { children: (company: Company) => ReactNode }) {
  const requested = useSearchParams().get("company");
  const companies = useQuery(companiesQuery());
  const company = requested
    ? companies.data?.find((c) => c.id === requested)
    : (companies.data?.find((c) => c.agents > 0) ?? companies.data?.[0]);

  const unauthorized = companies.error instanceof ApiError && companies.error.status === 401;
  useEffect(() => {
    if (unauthorized) storeToken(null);
  }, [unauthorized]);

  if (companies.isPending) return message("載入中…");
  if (companies.error) return message(`無法載入公司列表：${companies.error.message}`, true);
  if (!company) {
    return message(
      requested ? `找不到公司 ${requested}` : "還沒有任何公司。先執行 backend/scripts/seed_echo.py 建立示範公司。",
      true,
    );
  }
  return <>{children(company)}</>;
}

/** Links between admin pages keep the selected company. */
export function withCompany(path: string, companyId: string): string {
  return `${path}?company=${encodeURIComponent(companyId)}`;
}

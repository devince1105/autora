"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { CompanyScope, withCompany, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";
import { ConnectionBadge } from "@/features/dashboard/DashboardView";
import { connectionModel } from "@/features/dashboard/model";
import { useNow } from "@/hooks/useNow";
import { OfficeCanvas, parseView, type OfficeView } from "@/office3d/OfficeCanvas";
import { useRealtime } from "@/stores/realtime";

const VIEWS: { id: OfficeView; label: string }[] = [
  { id: "auto", label: "自動" },
  { id: "3d", label: "3D" },
  { id: "2d", label: "2D" },
];

/** /office: the company's office. ?view=3d|2d overrides the automatic choice. */
export function OfficePage() {
  return <CompanyScope>{(company) => <CompanyOffice company={company} />}</CompanyScope>;
}

function CompanyOffice({ company }: { company: Company }) {
  useCompanyStream(company.id);
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const view = parseView(params.get("view"));
  const setView = (next: OfficeView) => {
    const query = new URLSearchParams(params);
    if (next === "auto") query.delete("view");
    else query.set("view", next);
    router.replace(`${pathname}?${query}`);
  };
  const hasData = useRealtime((s) => s.company?.companyId === company.id);
  const connection = useRealtime((s) => s.connection);
  const now = useNow();

  return (
    <main className="flex h-dvh flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <p className="text-xs tracking-widest text-muted uppercase">Office</p>
          <h1 className="text-lg font-semibold">{company.name} 的辦公室</h1>
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div role="group" aria-label="顯示方式" className="flex rounded-lg border border-line p-0.5">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                type="button"
                aria-pressed={view === v.id}
                onClick={() => setView(v.id)}
                className={`rounded-md px-3 py-1 text-sm ${view === v.id ? "bg-accent text-canvas" : "text-muted"}`}
              >
                {v.label}
              </button>
            ))}
          </div>
          <Link href={withCompany("/dashboard", company.id)} className="text-sm text-accent underline">
            Dashboard
          </Link>
          <ConnectionBadge connection={connectionModel(connection, hasData, now)} />
        </div>
      </header>
      <div className="min-h-0 flex-1">
        <OfficeCanvas view={view} onViewChange={setView} />
      </div>
    </main>
  );
}

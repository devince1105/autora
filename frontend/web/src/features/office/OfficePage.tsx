"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { approvalsQuery, kpisQuery, orgQuery } from "@/api/queries";
import { AgentPanel } from "@/features/agent-panel/AgentPanel";
import { CompanyScope, withCompany, type Company } from "@/features/company/CompanyScope";
import { useCompanyStream } from "@/features/company/useCompanyStream";
import { ConnectionBadge } from "@/features/dashboard/DashboardView";
import { connectionModel, dashboardModel } from "@/features/dashboard/model";
import { useNow } from "@/hooks/useNow";
import { OfficeCanvas, terminalVars, parseView, type OfficeView } from "@/office3d/OfficeCanvas";
import { useRealtime, type RealtimeState } from "@/stores/realtime";
import { useUi } from "@/stores/ui";

import { DepartmentStrip, departmentNames as departmentNames_, departmentsOf } from "./Departments";
import { MiniDashboardView } from "./MiniDashboard";

const VIEWS: { id: OfficeView; label: string }[] = [
  { id: "auto", label: "自動" },
  { id: "3d", label: "3D" },
  { id: "2d", label: "2D" },
];

/**
 * /office (T-411): the office (3D or the 2D board), the dashboard's numbers in a strip, the
 * connection, and the agent detail panel of whoever is selected — by clicking an avatar or a card.
 * ?view=3d|2d overrides the automatic choice; ?department=<key> opens inside one department.
 */
export function OfficePage() {
  return <CompanyScope>{(company) => <CompanyOffice company={company} />}</CompanyScope>;
}

/**
 * Keeps ?department=<key> and the entered department in step (T-600 batch 3).
 *
 * The URL is the shareable half: a link to a department opens inside it, and entering one from
 * the strip puts it in the address bar. The roster is what resolves the key to a room, so the
 * link waits for the stream rather than guessing where the department is.
 */
function useDepartmentInUrl(realtime: ReturnType<typeof useRealtime<RealtimeState | null>>) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const wanted = params.get("department");
  const entered = useUi((s) => s.focusedDepartment);
  const enter = useUi((s) => s.enterDepartment);
  const known = useMemo(() => departmentsOf(Object.values(realtime?.agents ?? {})), [realtime]);
  /** The last key taken *from* the URL, so leaving a room is not read as a link into it. */
  const applied = useRef<string | null>(null);

  useEffect(() => {
    const key = entered?.key ?? null;
    const setUrl = (next: string | null) => {
      const query = new URLSearchParams(params);
      if (next) query.set("department", next);
      else query.delete("department");
      applied.current = next;
      router.replace(`${pathname}?${query}`);
    };

    if (wanted === key) {
      applied.current = key;
      return;
    }
    if (wanted && applied.current !== wanted) {
      // a link into a department. The roster is what resolves it to a room and arrives on the
      // stream a moment later, so until it does the link stands rather than being erased.
      const found = known.find((d) => d.key === wanted);
      if (found) {
        applied.current = wanted;
        enter({ key: found.key, zone: found.zone });
      } else if (known.length) {
        setUrl(key); // this company has no such department: the address bar follows the office
      }
      return;
    }
    setUrl(key); // the operator entered or left a room
  }, [wanted, entered, enter, known, params, pathname, router]);
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
  const realtime = useRealtime((s) => (s.company?.companyId === company.id ? s.company : null));
  useDepartmentInUrl(realtime);
  const connection = useRealtime((s) => s.connection);
  const now = useNow();
  const kpis = useQuery(kpisQuery(company.id));
  const org = useQuery(orgQuery(company.id));
  const departmentNames = useMemo(() => departmentNames_(org.data), [org.data]);
  const pending = useQuery(approvalsQuery(company.id));
  const model = dashboardModel(realtime, kpis.data, connection, now);
  // what the office settled on (``auto`` is decided in the canvas, by asking the browser)
  const [mode, setMode] = useState<"2d" | "3d">("3d");

  return (
    <main
      className="flex h-dvh flex-col bg-canvas text-ink"
      data-terminal={mode === "2d"}
      // the 2D office is a terminal; while it is on screen the page around it wears the same
      // colours, by overriding the app's own tokens here and nowhere else (D-007)
      style={mode === "2d" ? terminalVars() : undefined}
    >
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
          <Link href={withCompany("/admin/dashboard", company.id)} className="text-sm text-accent underline">
            Dashboard
          </Link>
          <ConnectionBadge connection={connectionModel(connection, realtime !== null, now)} />
        </div>
      </header>
      <MiniDashboardView model={model} pendingApprovals={pending.data?.length ?? null} />
      <DepartmentStrip companyId={company.id} />
      <div className="min-h-0 flex-1">
        {/* the detail panel is max-w-md (448 px) on the right while someone is selected */}
        <OfficeCanvas
          view={view}
          onViewChange={setView}
          onMode={setMode}
          selectionInsetRight={448}
          departmentNames={departmentNames}
        />
      </div>
      <AgentPanel />
    </main>
  );
}

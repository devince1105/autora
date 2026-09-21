"use client";

// The departments of the company, as the way into them (T-600 batch 3, ARCHITECTURE_V2 §14.7).
//
// The office is a digital twin of the organisation, so the way to look at one part of it is to
// enter the department that does that work. This strip is that door: one button per department
// the roster actually has, with how many people are in it, and one that steps back out to the
// whole floor.
//
// It invents nothing. The departments come from the agents on the stream (their `department_key`
// and the room their department occupies); a company with no org chart yet shows the parts of
// the floor its agents sit in, which is the honest answer to "what departments does it have".
//
// **The names come from the server** (ARCHITECTURE_V2 §14.7): a department is called what the
// org chart calls it. The frontend only names the rooms of its own floor plan, for a company
// whose departments are not on the chart yet.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { orgQuery } from "@/api/queries";
import type { components } from "@/api/schema.gen";
import { businessColors, DEPARTMENT_LABEL } from "@/office3d/OfficeCanvas";
import { useRealtime } from "@/stores/realtime";
import { useUi, type EnteredDepartment } from "@/stores/ui";

export interface DepartmentEntry extends EnteredDepartment {
  label: string;
  headcount: number;
  business: string | null;
  /** The colour the office marks this business with; null for a company-wide function. */
  businessColor: string | null;
}

interface RosterAgent {
  department_key: string | null;
  office_zone_key: string | null;
  business_unit_key: string | null;
  role: string;
}

/**
 * One entry per department on the roster, in a stable order, with its headcount.
 *
 * `names` is the org chart's answer to what each department is called; without it the key is
 * shown, which is better than a name the frontend made up.
 */
export function departmentsOf(
  agents: readonly RosterAgent[],
  names: Readonly<Record<string, string>> = {},
): DepartmentEntry[] {
  const colors = businessColors(agents.map((a) => a.business_unit_key ?? null));
  const found = new Map<string, DepartmentEntry>();
  for (const agent of agents) {
    const key = agent.department_key ?? agent.office_zone_key;
    if (!key) continue; // nobody's department: it is in the lists, not a room to enter
    const entry = found.get(key);
    if (entry) entry.headcount += 1;
    else
      found.set(key, {
        key,
        zone: agent.office_zone_key ?? key,
        label: names[key] ?? DEPARTMENT_LABEL[key] ?? key,
        headcount: 1,
        business: agent.business_unit_key ?? null,
        businessColor: agent.business_unit_key ? colors[agent.business_unit_key] : null,
      });
  }
  return [...found.values()].sort(
    (a, b) => (a.business ?? "").localeCompare(b.business ?? "") || a.key.localeCompare(b.key),
  );
}

type Org = components["schemas"]["OrgOut"];
type OrgDepartment = components["schemas"]["DepartmentOut"];

/** key -> name, from the org chart: the shared functions and every business's departments. */
export function departmentNames(org: Org | undefined): Record<string, string> {
  const names: Record<string, string> = {};
  const walk = (departments: readonly OrgDepartment[] | undefined) => {
    for (const department of departments ?? []) {
      names[department.key] = department.name;
      walk(department.teams);
    }
  };
  for (const unit of [org?.shared, ...(org?.units ?? [])]) walk(unit?.departments);
  return names;
}

/** How many people the office is not drawing right now, because they work somewhere else. */
export function elsewhere(departments: readonly DepartmentEntry[], entered: EnteredDepartment | null): number {
  if (!entered) return 0;
  return departments
    .filter((d) => d.key !== entered.key)
    .reduce((total, d) => total + d.headcount, 0);
}

export function DepartmentStrip({ companyId }: { companyId: string }) {
  const agents = useRealtime((s) => s.company?.agents);
  const entered = useUi((s) => s.focusedDepartment);
  const enter = useUi((s) => s.enterDepartment);
  const org = useQuery(orgQuery(companyId));
  const names = useMemo(() => departmentNames(org.data), [org.data]);
  const departments = useMemo(
    () => departmentsOf(Object.values(agents ?? {}), names),
    [agents, names],
  );
  if (departments.length === 0) return null;

  return (
    <div
      role="group"
      aria-label="部門"
      className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-sm"
    >
      <button
        type="button"
        data-testid="department-all"
        aria-pressed={entered === null}
        onClick={() => enter(null)}
        className={`rounded-md px-2.5 py-1 ${entered === null ? "bg-accent text-canvas" : "text-muted"}`}
      >
        整層
      </button>
      {departments.map((department) => (
        <button
          key={department.key}
          type="button"
          data-testid={`department-${department.key}`}
          aria-pressed={entered?.key === department.key}
          onClick={() =>
            enter(
              entered?.key === department.key
                ? null
                : { key: department.key, zone: department.zone },
            )
          }
          className={`rounded-md px-2.5 py-1 ${
            entered?.key === department.key ? "bg-accent text-canvas" : "text-muted"
          }`}
        >
          {department.businessColor ? (
            <span
              aria-hidden
              data-testid={`department-business-${department.key}`}
              className="mr-1.5 inline-block size-2 rounded-full align-middle"
              style={{ backgroundColor: department.businessColor }}
            />
          ) : null}
          {department.label}
          <span className="ml-1.5 tabular-nums opacity-70">{department.headcount}</span>
        </button>
      ))}
      {/* inside a room the office draws only its people; say who is not on screen (§14.7) */}
      {entered && elsewhere(departments, entered) > 0 ? (
        <span data-testid="department-elsewhere" className="ml-1 text-xs text-muted">
          其他部門 {elsewhere(departments, entered)} 人不在畫面上
        </span>
      ) : null}
    </div>
  );
}

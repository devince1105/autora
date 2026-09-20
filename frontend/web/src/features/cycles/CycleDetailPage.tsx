"use client";

import { useSuspenseQuery } from "@tanstack/react-query";

import { cycleQuery } from "@/api/queries";

import { CycleDetailView } from "./CycleDetailView";

export function CycleDetailPage({ cycleId }: { cycleId: string }) {
  const { data } = useSuspenseQuery(cycleQuery(cycleId));
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
      {<CycleDetailView cycle={data} />}
    </main>
  );
}

import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { CycleDetailPage } from "@/features/cycles/CycleDetailPage";

export const metadata = { title: "營運週期 · Autora" };

export default async function Page({ params }: { params: Promise<{ cycleId: string }> }) {
  const { cycleId } = await params;
  return (
    <TokenGate>
      <Suspense>
        <CycleDetailPage cycleId={cycleId} />
      </Suspense>
    </TokenGate>
  );
}

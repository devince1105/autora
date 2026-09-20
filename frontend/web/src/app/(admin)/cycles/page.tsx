import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { CyclesPage } from "@/features/cycles/CyclesPage";

export const metadata = { title: "營運週期 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <CyclesPage />
      </Suspense>
    </TokenGate>
  );
}

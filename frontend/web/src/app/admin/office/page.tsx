import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { OfficePage } from "@/features/office/OfficePage";

export const metadata = { title: "辦公室 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <OfficePage />
      </Suspense>
    </TokenGate>
  );
}

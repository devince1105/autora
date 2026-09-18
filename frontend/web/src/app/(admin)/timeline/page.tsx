import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { TimelinePage } from "@/features/timeline/TimelinePage";

export const metadata = { title: "事件時間軸 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <TimelinePage />
      </Suspense>
    </TokenGate>
  );
}

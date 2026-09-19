import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { SourcesPage } from "@/features/newsroom/pages";

export const metadata = { title: "新聞室來源 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <SourcesPage />
      </Suspense>
    </TokenGate>
  );
}

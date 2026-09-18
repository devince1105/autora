import { Suspense } from "react";

import { ApprovalsPage } from "@/features/approvals/ApprovalsPage";
import { TokenGate } from "@/features/auth/TokenGate";

export const metadata = { title: "審批收件匣 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <ApprovalsPage />
      </Suspense>
    </TokenGate>
  );
}

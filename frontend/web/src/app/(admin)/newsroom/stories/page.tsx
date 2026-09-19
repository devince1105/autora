import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { StoriesPage } from "@/features/newsroom/pages";

export const metadata = { title: "新聞室題材 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <StoriesPage />
      </Suspense>
    </TokenGate>
  );
}

import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { ArticlesPage } from "@/features/newsroom/pages";

export const metadata = { title: "新聞室文章 · Autora" };

export default function Page() {
  return (
    <TokenGate>
      <Suspense>
        <ArticlesPage />
      </Suspense>
    </TokenGate>
  );
}

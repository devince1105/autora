import { Suspense } from "react";

import { TokenGate } from "@/features/auth/TokenGate";
import { ArticlePage } from "@/features/newsroom/pages";

export const metadata = { title: "文章 · 新聞室 · Autora" };

export default async function Page({ params }: { params: Promise<{ articleId: string }> }) {
  const { articleId } = await params;
  return (
    <TokenGate>
      <Suspense>
        <ArticlePage articleId={articleId} />
      </Suspense>
    </TokenGate>
  );
}

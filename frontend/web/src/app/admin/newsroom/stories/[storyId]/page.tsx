import { TokenGate } from "@/features/auth/TokenGate";
import { StoryPage } from "@/features/newsroom/pages";

export const metadata = { title: "題材 · 新聞室 · Autora" };

export default async function Page({ params }: { params: Promise<{ storyId: string }> }) {
  const { storyId } = await params;
  return (
    <TokenGate>
      <StoryPage storyId={storyId} />
    </TokenGate>
  );
}

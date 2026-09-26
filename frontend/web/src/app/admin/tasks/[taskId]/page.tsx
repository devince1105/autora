import { TokenGate } from "@/features/auth/TokenGate";
import { TaskPage } from "@/features/trace-viewer/TracePage";

export const metadata = { title: "Task · Autora" };

export default async function Page({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  return (
    <TokenGate>
      <TaskPage taskId={taskId} />
    </TokenGate>
  );
}

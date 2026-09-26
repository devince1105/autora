import { redirect } from "next/navigation";

export default async function Page({ searchParams }: { searchParams: Promise<{ company?: string }> }) {
  const { company } = await searchParams;
  redirect(company ? `/admin/newsroom/articles?company=${encodeURIComponent(company)}` : "/admin/newsroom/articles");
}

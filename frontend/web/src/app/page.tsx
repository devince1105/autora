import { redirect } from "next/navigation";

/** The site's front door is the newsroom's front page (D-015); the admin pages are under /admin (/admin/dashboard). */
export default function HomePage() {
  redirect("/news/zh-TW");
}

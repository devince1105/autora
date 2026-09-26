import { Suspense } from "react";

import { AdminLogin } from "@/features/auth/AdminLogin";

export const metadata = { title: "登入後台 · Autora" };

export default function Page() {
  return (
    <Suspense>
      <AdminLogin />
    </Suspense>
  );
}

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The browser tests (e2e/) build their own copy against their own API, next to the dev build.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // That build skips type checking: `pnpm typecheck` owns it, and the shared tsconfig also
  // includes the dev server's generated types, which the e2e build must not depend on.
  typescript: { ignoreBuildErrors: Boolean(process.env.NEXT_DIST_DIR) },
};

export default nextConfig;

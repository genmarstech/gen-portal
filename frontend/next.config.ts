import type { NextConfig } from "next";

/**
 * NOT a static export — unlike gen-website.
 *
 * Auth needs a server: sessions, CSRF, and redirects that depend on who is
 * signed in. That is the deliberate cost of this application, and precisely why
 * it lives in its own repo rather than inside the marketing site, which keeps
 * its no-Node runtime image.
 */
const nextConfig: NextConfig = {
  /**
   * Standalone output: `next build` emits a self-contained server plus only the
   * node_modules it actually traced. The runtime image copies that instead of
   * the full dependency tree, which is both smaller and a smaller attack
   * surface — the build toolchain never reaches production.
   */
  output: "standalone",

  reactStrictMode: true,
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },

  /**
   * The API is same-origin in production: the host Caddy routes /api/* to
   * Django on :8010 and everything else here on :3010. One origin means session
   * cookies work with SameSite=Lax and there is no CORS to configure.
   * In development, proxy it so the same relative paths work.
   */
  async rewrites() {
    const api = process.env.API_ORIGIN ?? "http://127.0.0.1:8010";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;

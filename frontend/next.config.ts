import type { NextConfig } from "next";

// Script sources are not restricted here: App Router pages use inline bootstrap scripts, and a nonce-based
// CSP would force every page to render dynamically. These directives are safe with static pages.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), display-capture=(self), geolocation=(), payment=()",
  },
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
  },
];

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // Candidate links carry the invite token in the path: never send it to another site as a referrer.
      { source: "/join/:path*", headers: [{ key: "Referrer-Policy", value: "no-referrer" }] },
    ];
  },
  // Self-contained server bundle for the Docker image; Vercel ignores this setting.
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

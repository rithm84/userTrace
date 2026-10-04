import type { NextConfig } from "next";

import { PROXIED_NEXT_PREFIX } from "./lib/next-assets";

/**
 * The front door terminates public TLS for `<opaque>.preview.utrace.dev` and performs no
 * client product build. Every response is uncacheable, so no CDN caching is configured here.
 */
const nextConfig: NextConfig = {
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  devIndicators: false,
  async rewrites() {
    // The proxied product's `/_next/*` assets would otherwise be answered by this
    // deployment's own static layer; see lib/next-assets.ts.
    return {
      beforeFiles: [{ source: "/_next/:path*", destination: `${PROXIED_NEXT_PREFIX}/:path*` }],
      afterFiles: [],
      fallback: [],
    };
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          {
            key: "Strict-Transport-Security",
            value: "max-age=63072000; includeSubDomains; preload",
          },
        ],
      },
    ];
  },
};

export default nextConfig;

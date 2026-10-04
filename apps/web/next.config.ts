import type { NextConfig } from "next";
import path from "node:path";
import { buildContentSecurityPolicy } from "./lib/csp";

const riveReactEntryPoint =
  "./node_modules/@rive-app/react-webgl2/dist/index.js";
const riveWebglEntryPoint = "./node_modules/@rive-app/webgl2/rive.js";

const riveAliases = {
  "@rive-app/react-webgl2": path.resolve(
    process.cwd(),
    riveReactEntryPoint,
  ),
  "@rive-app/webgl2": path.resolve(process.cwd(), riveWebglEntryPoint),
};

const configureWebpack: NonNullable<NextConfig["webpack"]> = (config) => {
  const resolve = config.resolve;
  if (resolve === undefined) {
    throw new Error("Next.js Webpack configuration must define resolve options");
  }
  if (Array.isArray(resolve.alias)) {
    throw new TypeError("Next.js Webpack aliases must use object form");
  }
  return {
    ...config,
    resolve: {
      ...resolve,
      alias: {
        ...resolve.alias,
        ...riveAliases,
      },
    },
  };
};

const nextConfig: NextConfig = {
  ...(process.env.UTRACE_PREVIEW_DIST_DIR ? { distDir: process.env.UTRACE_PREVIEW_DIST_DIR } : {}),
  transpilePackages: ["@utrace/agent-character"],
  images: { qualities: [70, 75, 90] },
  turbopack: {
    root: path.resolve(process.cwd(), "../.."),
    resolveAlias: {
      "@rive-app/react-webgl2": riveReactEntryPoint,
      "@rive-app/webgl2": riveWebglEntryPoint,
    },
  },
  webpack: configureWebpack,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  devIndicators: false,
  productionBrowserSourceMaps: false,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: buildContentSecurityPolicy(process.env),
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
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

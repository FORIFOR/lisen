import type { NextConfig } from "next";

// All API calls go through this Next.js server (/api/* -> FastAPI), so other devices
// (Windows / iPhone over LAN or Tailscale) only need to reach one port.
const BACKEND_URL = process.env.LISTENFLOW_API_URL ?? `http://127.0.0.1:${process.env.LISTENFLOW_API_PORT ?? "8765"}`;

const nextConfig: NextConfig = {
  cacheComponents: true,
  experimental: {
    // NotebookLM audio uploads (/api/v1/imports/notebooklm) are proxied to FastAPI, which caps them at 300MB.
    // The default 10MB proxy limit would silently truncate them.
    proxyClientMaxBodySize: "310mb",
    // lesson generation with Gemini TTS calls the API once per line and can take over a minute;
    // the default proxy timeout (30 s) cut those requests off with "socket hang up"
    proxyTimeout: 300_000,
  },
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/:path*` }];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

// The Python API (clipper serve) runs on the same Mac. Proxying /api and /media through
// Next keeps the browser on one origin, so phones on the same Wi-Fi only need port 3210.
const API = process.env.CLIPPER_API ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  devIndicators: { position: "bottom-right" },
  // Lets a phone on the same Wi-Fi use the dev server via the Mac's Bonjour name (e.g. anas-mbp.local).
  allowedDevOrigins: ["*.local"],
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/media/:path*", destination: `${API}/media/:path*` },
    ];
  },
};

export default nextConfig;

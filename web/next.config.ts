import type { NextConfig } from "next";

// /api and /media are forwarded to the clipper API by proxy.ts (which also handles sign-in
// and the shared key when hosted).
const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  devIndicators: { position: "bottom-right" },
  // Lets a phone on the same Wi-Fi use the dev server via the Mac's Bonjour name (e.g. anas-mbp.local).
  allowedDevOrigins: ["*.local"],
};

export default nextConfig;

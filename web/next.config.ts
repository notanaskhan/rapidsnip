import type { NextConfig } from "next";

// /api and /media are forwarded to the clipper API by proxy.ts (which also handles sign-in
// and the shared key when hosted).
const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  devIndicators: { position: "bottom-right" },
  // Lets a phone use the dev server via the Mac's Bonjour name on the same Wi-Fi (e.g. anas-mbp.local)
  // or its Tailscale MagicDNS name from anywhere (e.g. anas-mbp.tail1234.ts.net).
  allowedDevOrigins: ["*.local", "*.ts.net"],
};

export default nextConfig;

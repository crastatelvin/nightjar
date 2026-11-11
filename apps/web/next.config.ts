import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  typedRoutes: false,
  // The dev server treats localhost as its own origin. Opening the app at
  // 127.0.0.1 is common in containers and remote shells, so allow that
  // hostname for dev assets and HMR instead of silently breaking hydration.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;

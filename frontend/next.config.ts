import type { NextConfig } from "next";

const phoneDevOrigins = (process.env.NIANNIAN_ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((item) => item.trim().toLowerCase())
  .filter(Boolean);

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1", ...phoneDevOrigins],
  reactStrictMode: true,
  async rewrites() {
    const backendUrl = process.env.NIANNIAN_BACKEND_URL ?? "http://127.0.0.1:8011";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;

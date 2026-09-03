import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { proxy } from "@/proxy";

describe("入口安全策略", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("正式家庭空间允许同源麦克风", async () => {
    vi.stubEnv("FORMAL_AUTH_REQUIRED", "true");
    vi.stubEnv("DEMO_PUBLIC_MODE", "0");
    const request = new NextRequest("https://formal.example.com/record", {
      headers: { cookie: "lingnian_session=test-session" },
    });

    const response = await proxy(request);

    expect(response.headers.get("Permissions-Policy")).toBe(
      "camera=(), microphone=(self), geolocation=()",
    );
  });

  it("公网只读演示仍禁止麦克风", async () => {
    vi.stubEnv("FORMAL_AUTH_REQUIRED", "false");
    vi.stubEnv("DEMO_PUBLIC_MODE", "1");
    const request = new NextRequest("https://demo.example.com/demo-login");

    const response = await proxy(request);

    expect(response.headers.get("Permissions-Policy")).toBe(
      "camera=(), microphone=(), geolocation=()",
    );
  });
});

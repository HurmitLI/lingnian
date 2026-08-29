import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

describe("本机 API 错误处理", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("保留后端给出的明确错误并标记可重试状态", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: "TEMPORARY", message: "服务正在恢复" } }),
      { status: 503, headers: { "Content-Type": "application/json" } },
    )));

    await expect(api("/test")).rejects.toMatchObject({
      code: "TEMPORARY",
      message: "服务正在恢复",
      retryable: true,
    });
  });

  it("网络中断时返回家人可理解的提示", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const result = api("/test").catch((value: unknown) => value);
    await expect(result).resolves.toBeInstanceOf(ApiError);
    await expect(result).resolves.toMatchObject({ code: "NETWORK_UNAVAILABLE", retryable: true });
  });
});

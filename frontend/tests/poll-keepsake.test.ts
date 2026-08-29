import { describe, expect, it, vi } from "vitest";

import { pollKeepsake } from "@/lib/keepsake/poll-keepsake";
import type { Keepsake } from "@/lib/types";

function keepsake(status: string, progress = 0): Keepsake {
  return {
    id: "keepsake-1",
    elder_id: "elder-1",
    authorization_id: "authorization-1",
    version: 1,
    title: "测试念想",
    story_manifest: [],
    status,
    progress,
    attempt: 1,
    error_code: null,
    mime_type: "video/mp4",
    duration_ms: status === "ready" ? 1200 : null,
    width: 1280,
    height: 720,
    size_bytes: status === "ready" ? 1024 : null,
    renderer: "local_ffmpeg",
    cost_cents: 0,
    source_mode: "original_audio_only",
    content_url: status === "ready" ? "/api/v1/keepsakes/keepsake-1/content" : null,
    created_at: "2026-08-29T08:00:00Z",
    updated_at: "2026-08-29T08:00:00Z",
  };
}

describe("视频长任务轮询", () => {
  it("持续报告后端进度并在视频就绪后停止", async () => {
    const onUpdate = vi.fn();
    const fetchKeepsake = vi.fn()
      .mockResolvedValueOnce(keepsake("rendering", 55))
      .mockResolvedValueOnce(keepsake("ready", 100));
    const result = await pollKeepsake(keepsake("queued"), fetchKeepsake, {
      sleep: async () => undefined,
      onUpdate,
    });
    expect(result.status).toBe("ready");
    expect(fetchKeepsake).toHaveBeenCalledTimes(2);
    expect(onUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ progress: 100 }));
  });

  it("页面卸载时停止后续请求", async () => {
    const fetchKeepsake = vi.fn();
    const result = await pollKeepsake(keepsake("queued"), fetchKeepsake, {
      isCancelled: () => true,
      sleep: async () => undefined,
    });
    expect(result.status).toBe("queued");
    expect(fetchKeepsake).not.toHaveBeenCalled();
  });

  it("临时断网后重试，不把任务误判为失败", async () => {
    const onTemporaryError = vi.fn();
    const fetchKeepsake = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(keepsake("ready", 100));
    const result = await pollKeepsake(keepsake("rendering", 20), fetchKeepsake, {
      sleep: async () => undefined,
      onTemporaryError,
    });
    expect(result.status).toBe("ready");
    expect(onTemporaryError).toHaveBeenCalledOnce();
  });
});

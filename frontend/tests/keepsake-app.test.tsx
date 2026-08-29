import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import KeepsakeApp from "@/features/keepsake/keepsake-app";

const apiMock = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/keepsake" }));
vi.mock("@/lib/api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  apiDownload: vi.fn(),
  mediaUrl: (path: string | null) => path,
}));

const profile = {
  id: "elder-test",
  family_id: "family-test",
  data_classification: "authorized_sensitive",
  display_name: "测试奶奶",
  preferred_name: "奶奶",
  birth_year: null,
  birth_era: null,
  native_place: null,
  occupation_summary: null,
};

describe("原声视频念想表单", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/keepsake");
    apiMock.mockImplementation((path: string) => {
      if (path === "/api/v1/elder-profiles") return Promise.resolve([profile]);
      if (path.endsWith("/keepsake-catalog")) return Promise.resolve([{
        story_id: "story-1",
        title: "河边的小路",
        life_stage: "童年",
        confirmed_at: "2026-08-29T08:00:00Z",
        has_original_audio: true,
        audio_asset_id: "audio-1",
        image_asset_id: null,
        unavailable_reason: null,
      }]);
      if (path.endsWith("/keepsakes")) return Promise.resolve([]);
      return Promise.reject(new Error(`unexpected ${path}`));
    });
  });

  afterEach(() => {
    cleanup();
    apiMock.mockReset();
  });

  it("未选择故事并逐项确认前禁止开始制作", async () => {
    render(<KeepsakeApp />);
    const submit = await screen.findByRole("button", { name: "确认授权并开始本机制作" });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/河边的小路/));
    expect(submit).toBeDisabled();
    fireEvent.click(screen.getByLabelText("我确认有权使用这次所选的原始录音。"));
    fireEvent.click(screen.getByLabelText("这份视频只用于家庭记忆保存。"));
    fireEvent.click(screen.getByLabelText("不会把视频用于仿冒、误导或冒充本人。"));
    fireEvent.click(screen.getByLabelText("本次只使用亲口说过的原始声音，不生成新语音。"));

    await waitFor(() => expect(submit).toBeEnabled());
    expect(screen.getByText("素材不离开本机")).toBeVisible();
    expect(screen.getByText("新增费用 0 元")).toBeVisible();
  });
});

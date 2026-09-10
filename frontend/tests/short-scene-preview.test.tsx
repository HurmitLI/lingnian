import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ShortScenePreview from "@/features/memory/short-scene-preview";
import { api } from "@/lib/api";
import type { TimelineItem } from "@/lib/types";

vi.mock("@/lib/api", () => ({ api: vi.fn(), mediaUrl: (url: string) => url }));
const story = { story: { id: "story", title: "车站回忆" }, source_session_id: "session", audio_url: "/audio" } as TimelineItem;

describe("10秒原声预览", () => {
  afterEach(() => { cleanup(); vi.clearAllMocks(); });
  it("没有故事时不显示虚假的生成按钮", () => {
    render(<ShortScenePreview timeline={[]} />);
    expect(screen.getByText(/完成一次采访并保存故事/)).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("缺时间轴时解释原因且不提交生成", async () => {
    vi.mocked(api).mockResolvedValue({ status: "needs_source_timing", candidates: [], generation_ready: false });
    render(<ShortScenePreview timeline={[story]} />);
    fireEvent.click(screen.getByRole("button", { name: "查看约10秒原声候选" }));
    expect(await screen.findByText(/不需要你重新采访/)).toBeVisible();
    expect(api).toHaveBeenCalledOnce();
    expect(api).toHaveBeenCalledWith("/api/v1/memory-sessions/session/short-scene-preview", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });
  it("展示真实截取时间与原声，不声称已生成", async () => {
    vi.mocked(api).mockResolvedValue({ status: "candidates_ready", generation_ready: false, candidates: [
      { id: "candidate", text: "我在站台等车。", start_frame: 176000, end_frame: 320000, sample_rate: 16000, duration_seconds: 9 },
    ] });
    render(<ShortScenePreview timeline={[story]} />);
    fireEvent.click(screen.getByRole("button", { name: "查看约10秒原声候选" }));
    expect(await screen.findByText("我在站台等车。")).toBeVisible();
    expect(screen.getByLabelText("试听原声 11.0 至 20.0 秒")).toHaveAttribute("src", "/audio#t=11,20");
    expect(screen.getByText(/不代表影片已经生成/)).toBeVisible();
  });
  it("切换故事时撤销旧请求，不显示旧片段", async () => {
    let finish: ((value: unknown) => void) | undefined;
    vi.mocked(api).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<ShortScenePreview timeline={[story, { ...story, story: { ...story.story, id: "other", title: "另一段" }, source_session_id: "other-session" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "查看约10秒原声候选" }));
    const signal = vi.mocked(api).mock.calls[0][1]?.signal;
    fireEvent.change(screen.getByLabelText("选择回忆原声"), { target: { value: "other" } });
    await waitFor(() => expect(signal?.aborted).toBe(true));
    finish?.({ status: "no_complete_excerpt", candidates: [] });
    expect(screen.queryByText(/暂时没有找到4/)).not.toBeInTheDocument();
  });
});

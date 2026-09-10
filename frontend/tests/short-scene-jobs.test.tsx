import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ShortSceneJobs from "@/features/memory/short-scene-jobs";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn(), mediaUrl: (path: string) => path }));
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.useRealTimers(); });
const job = { id: "job", plan_id: "plan", status: "queued", progress_percent: 0, result_asset_id: null };

it("只读查看新队列，排队不冒充正在生成", async () => {
  vi.mocked(api).mockResolvedValue([job]);
  render(<ShortSceneJobs sessionId="session" />);
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  expect(await screen.findByText("等待生成节点领取 · 尚未开始生成")).toBeVisible();
  expect(api).toHaveBeenCalledWith("/api/v1/memory-sessions/session/short-scene-jobs", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.queryByText(/节点最近报告进度/)).not.toBeInTheDocument();
});

it("空列表不当成完成，也不自动创建制作任务", async () => {
  vi.mocked(api).mockResolvedValue([]);
  render(<ShortSceneJobs sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  expect(await screen.findByText(/这次采访还没有十秒制作任务/)).toBeVisible();
  expect(api).toHaveBeenCalledTimes(1);
});

it("取消需要单独确认，重复点击只发送一次", async () => {
  vi.mocked(api).mockResolvedValueOnce([job]).mockResolvedValueOnce({ ...job, status: "cancelled" });
  render(<ShortSceneJobs sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  fireEvent.click(await screen.findByRole("button", { name: "取消本次制作" }));
  expect(api).toHaveBeenCalledTimes(1);
  const cancel = screen.getByRole("button", { name: "确认取消本次制作" });
  fireEvent.click(cancel); fireEvent.click(cancel);
  expect(await screen.findByText("已取消任务授权")).toBeVisible();
  expect(screen.getByText(/不保证已经提交的显卡计算立即停止/)).toBeVisible();
  expect(api).toHaveBeenCalledTimes(2);
});

it("中断不提供重试，输入核对不自动批准", async () => {
  vi.mocked(api).mockResolvedValue([{ ...job, status: "interrupted" }, { ...job, id: "review", status: "awaiting_input_review" }]);
  render(<ShortSceneJobs sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  expect(await screen.findByText(/原计算可能仍在进行/)).toBeVisible();
  expect(screen.getByText(/旧版手动上传任务仍需核对原节点记录/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /重试|继续生成|通过/ })).not.toBeInTheDocument();
});

it("只展示待完整视听的候选，不自动加载或标记合格", async () => {
  vi.mocked(api).mockResolvedValue([{ ...job, status: "awaiting_full_playback_review", result_asset_id: "asset" }]);
  render(<ShortSceneJobs sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  const video = await screen.findByLabelText("待验收十秒候选 1");
  expect(video).toHaveAttribute("src", "/api/v1/media-assets/asset/content");
  expect(video).toHaveAttribute("preload", "none");
  expect(video).not.toHaveAttribute("autoplay");
  expect(screen.getByText(/这是待验收候选，不是合格成片/)).toBeVisible();
});

it("断网保留旧状态但明确过期，并停止自动查询和取消操作", async () => {
  vi.mocked(api).mockResolvedValueOnce([{ ...job, status: "generating", progress_percent: 20 }]).mockRejectedValue(new Error("连接断开"));
  render(<ShortSceneJobs sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  await screen.findByText("家庭生成服务正在处理");
  fireEvent.click(screen.getByRole("button", { name: "刷新十秒任务状态" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不能据此判断仍在生成");
  expect(screen.queryByRole("button", { name: "取消本次制作" })).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(2);
});

it("活动任务每十秒仅查询状态，完成后停止轮询", async () => {
  vi.useFakeTimers();
  vi.mocked(api).mockResolvedValueOnce([job]).mockResolvedValueOnce([{ ...job, status: "awaiting_full_playback_review" }]);
  render(<ShortSceneJobs sessionId="session" />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(api).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(api).toHaveBeenCalledTimes(2);
});

it("切换故事销毁请求，不显示上一场采访的结果", async () => {
  vi.mocked(api).mockImplementation(() => new Promise(() => {}));
  const view = render(<ShortSceneJobs key="one" sessionId="one" />);
  fireEvent.click(screen.getByRole("button", { name: "查看十秒制作任务" }));
  const signal = vi.mocked(api).mock.calls[0][1]?.signal;
  view.rerender(<ShortSceneJobs key="two" sessionId="two" />);
  expect(signal?.aborted).toBe(true);
  expect(screen.getByRole("button", { name: "查看十秒制作任务" })).toBeEnabled();
  expect(api).toHaveBeenCalledTimes(1);
});

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ShortSceneSelection from "@/features/memory/short-scene-selection";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const input = { status: "awaiting_purpose_specific_consent", input_sha256: "a".repeat(64), payload: { answers: [{ text: "我在车站等车。后来才到无锡。" }] } };
const plan = { id: "plan", input_sha256: input.input_sha256, status: "awaiting_scene_context_review", result: {
  candidate: { text: "我在车站等车。", duration_seconds: 9 }, selection: { reason: "一个等车场景", scene: {
    context_summary: "去无锡之前", opening_state: "站着等车", action: "自然站立", illustrative_details: ["长相是示意设计"],
    facts: [{ field: "era", status: "unknown", value: null }],
  } },
} };

it("只在明确授权后发送，并显示未生成影片的方案", async () => {
  vi.mocked(api).mockResolvedValueOnce(input).mockResolvedValueOnce([]).mockResolvedValueOnce(plan);
  render(<ShortSceneSelection sessionId="session" />);
  expect(api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  const submit = await screen.findByRole("button", { name: "同意并选择一个场景" });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(submit); fireEvent.click(submit);
  expect(await screen.findByText("待核对的单场景方案 · 尚未生成影片")).toBeVisible();
  expect(api).toHaveBeenCalledTimes(3);
  expect(JSON.parse(vi.mocked(api).mock.calls[2][1]?.body as string)).toEqual(expect.objectContaining({ authorize_text_send: true, input_sha256: input.input_sha256 }));
  expect(screen.getByText("原文未说明，不能当成已知事实")).toBeVisible();
});

it("刷新恢复已有方案，不重新发送", async () => {
  vi.mocked(api).mockResolvedValueOnce(input).mockResolvedValueOnce([plan]);
  render(<ShortSceneSelection sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  expect(await screen.findByText("待核对的单场景方案 · 尚未生成影片")).toBeVisible();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(api).toHaveBeenCalledTimes(2);
});

it("不明结果不冒充成功或重新开放发送", async () => {
  vi.mocked(api).mockResolvedValueOnce(input).mockResolvedValueOnce([{ ...plan, status: "outcome_unknown", result: {} }]);
  render(<ShortSceneSelection sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不会自动重发");
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

it("断网后优先查询已有请求，不偷偷重发", async () => {
  vi.mocked(api).mockResolvedValueOnce(input).mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("连接断开"));
  render(<ShortSceneSelection sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "同意并选择一个场景" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("查看选景结果");
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(api).toHaveBeenCalledTimes(3);
});

it("离开当前故事后撤销读取，不串入另一段采访", async () => {
  vi.mocked(api).mockImplementation(() => new Promise(() => {}));
  const rendered = render(<ShortSceneSelection sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  const signal = vi.mocked(api).mock.calls[0][1]?.signal;
  rendered.unmount();
  await waitFor(() => expect(signal?.aborted).toBe(true));
});

it("授权前明确展示提问和谁在讲谁，不把提问当成事实", async () => {
  vi.mocked(api).mockResolvedValueOnce({ ...input, payload: {
    answers: [{ text: "不是，他坐的是普通客车。", question: "您父亲坐的是蒸汽火车吗？" }],
    interview_context: { subject_label: "外公", narrator_label: "妈妈", narrator_is_subject: false },
  } }).mockResolvedValueOnce([]);
  render(<ShortSceneSelection sessionId="session" />);
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  fireEvent.click(await screen.findByText("查看本次发送的采访文字"));
  expect(screen.getByText(/记录对象：外公；讲述者：妈妈/)).toBeVisible();
  expect(screen.getByText(/提问（不是事实依据）：您父亲/)).toBeVisible();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(api).toHaveBeenCalledTimes(2);
});

it("有照片模式使用独立输入和授权，不沿用示意模式", async () => {
  vi.mocked(api).mockResolvedValueOnce(input).mockResolvedValueOnce([]).mockResolvedValueOnce({ ...plan, reference_mode: "user_photo" });
  render(<ShortSceneSelection sessionId="session" />);
  fireEvent.change(screen.getByLabelText("人物参考方式"), { target: { value: "user_photo" } });
  fireEvent.click(screen.getByRole("button", { name: "准备单场景方案" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "同意并选择一个场景" }));
  expect(await screen.findByText(/选择一张已获授权的原始照片即可/)).toBeVisible();
  expect(vi.mocked(api).mock.calls[0][0]).toContain("reference_mode=user_photo");
  expect(JSON.parse(vi.mocked(api).mock.calls[2][1]?.body as string).reference_mode).toBe("user_photo");
  fireEvent.change(screen.getByLabelText("人物参考方式"), { target: { value: "illustrative" } });
  expect(screen.queryByText(/选择一张已获授权的原始照片即可/)).not.toBeInTheDocument();
});

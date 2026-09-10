import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ShortSceneCreate, { checkReference } from "@/features/memory/short-scene-create";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));
const props = { sessionId: "session", planId: "plan", inputSha256: "a".repeat(64), referenceMode: "illustrative" as const };
const storageKey = "lingnian-short-attempt:session:plan";
const job = { id: "job", plan_id: "plan", status: "queued" };
let lockRequest: ReturnType<typeof vi.fn>;
beforeEach(() => {
  localStorage.clear();
  lockRequest = vi.fn(async (_name, _options, fn) => fn({ name: "lock" }));
  Object.defineProperty(navigator, "locks", { configurable: true, value: { request: lockRequest } });
  URL.createObjectURL = vi.fn(() => "blob:fixture");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); localStorage.clear(); });

function png(width = 1280, height = 704) {
  const bytes = new Uint8Array(24);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10], 0);
  bytes.set([73, 72, 68, 82], 12);
  const view = new DataView(bytes.buffer); view.setUint32(16, width); view.setUint32(20, height);
  const file = new File([bytes], "test.png", { type: "image/png" });
  vi.spyOn(file, "slice").mockReturnValue({ arrayBuffer: async () => bytes.buffer } as Blob);
  return file;
}

async function fill() {
  fireEvent.click(screen.getByRole("button", { name: "准备制作素材" }));
  const input = await screen.findByLabelText(/选择已准备的场景参考PNG/);
  fireEvent.change(input, { target: { files: [png()] } });
  await screen.findByAltText(/本次待核对的场景参考/);
  screen.getAllByRole("checkbox").forEach((checkbox) => fireEvent.click(checkbox));
}

it("先核对且明确授权，持久化请求键后只发送一次原声引用与PNG", async () => {
  vi.mocked(api).mockResolvedValueOnce([]).mockImplementationOnce(async (_path, options) => {
    expect(localStorage.getItem(storageKey)).toBeTruthy();
    expect(options?.body).toBeInstanceOf(FormData);
    const body = options?.body as FormData;
    expect(body.get("reference")).toBeInstanceOf(File);
    expect(JSON.parse(body.get("consent") as string)).toMatchObject({ authorize_node_delivery: true,
      authorize_material_export: true, reference_rights_confirmed: true, subject_consent: true, no_impersonation: true });
    return job;
  });
  render(<ShortSceneCreate {...props} />);
  expect(api).not.toHaveBeenCalled();
  await fill();
  const submit = screen.getByRole("button", { name: "授权素材投递并创建十秒任务" });
  fireEvent.click(submit); fireEvent.click(submit);
  expect(await screen.findByRole("status")).toHaveTextContent("这不代表已经开始生成");
  expect(api).toHaveBeenCalledTimes(2);
  expect(lockRequest).toHaveBeenCalledTimes(1);
});

it("结果不明后重新打开只按原键查询，查不到也不解锁重复创建", async () => {
  vi.mocked(api).mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("断网"));
  const first = render(<ShortSceneCreate {...props} />); await fill();
  fireEvent.click(screen.getByRole("button", { name: "授权素材投递并创建十秒任务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不会自动重新提交");
  const key = localStorage.getItem(storageKey);
  first.unmount();
  vi.mocked(api).mockResolvedValueOnce(null);
  render(<ShortSceneCreate {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "准备制作素材" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请求仍可能在处理中");
  expect(vi.mocked(api).mock.calls[2][0]).toBe(`/api/v1/memory-sessions/session/short-scene-plans/plan/jobs/by-request/${key}`);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(localStorage.getItem(storageKey)).toBe(key);
});

it("已保存的原请求恢复为同一任务，不重新上传", async () => {
  localStorage.setItem(storageKey, "saved-request");
  vi.mocked(api).mockResolvedValue(job);
  render(<ShortSceneCreate {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "准备制作素材" }));
  expect(await screen.findByRole("status")).toHaveTextContent("已找到这份方案");
  expect(api).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

it("同方案已有任务不另建，失败也不偷偷换键重试", async () => {
  vi.mocked(api).mockResolvedValue([{ ...job, status: "failed" }]);
  render(<ShortSceneCreate {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "准备制作素材" }));
  expect(await screen.findByRole("status")).toBeVisible();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
});

it("浏览器存储失败不能先上传再补请求记录", async () => {
  vi.mocked(api).mockResolvedValueOnce([]);
  render(<ShortSceneCreate {...props} />); await fill();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("存储不可用"); });
  fireEvent.click(screen.getByRole("button", { name: "授权素材投递并创建十秒任务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("存储不可用");
  expect(api).toHaveBeenCalledTimes(1);
});

it("另一标签页持锁时不发第二次上传", async () => {
  vi.mocked(api).mockResolvedValueOnce([]);
  lockRequest.mockImplementation(async (_name, _options, fn) => fn(null));
  render(<ShortSceneCreate {...props} />); await fill();
  fireEvent.click(screen.getByRole("button", { name: "授权素材投递并创建十秒任务" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("另一个页面正在提交");
  expect(api).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem(storageKey)).toBeNull();
});

it("更换图片撤销原授权，离开销毁本机预览", async () => {
  vi.mocked(api).mockResolvedValueOnce([]);
  const view = render(<ShortSceneCreate {...props} />); await fill();
  fireEvent.change(screen.getByLabelText(/选择已准备的场景参考PNG/), { target: { files: [png()] } });
  screen.getAllByRole("checkbox").forEach((checkbox) => expect(checkbox).not.toBeChecked());
  expect(screen.getByRole("button", { name: "授权素材投递并创建十秒任务" })).toBeDisabled();
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fixture");
  expect(api).toHaveBeenCalledTimes(1);
});

it("拒绝竖版人像或错误PNG头，不进行拉伸", async () => {
  await expect(checkReference(png(704, 1280))).rejects.toThrow("不要拉伸人物");
  const wrong = png();
  vi.mocked(wrong.slice).mockReturnValue({ arrayBuffer: async () => new Uint8Array(24).buffer } as Blob);
  await expect(checkReference(wrong)).rejects.toThrow("不要只改文件后缀");
});

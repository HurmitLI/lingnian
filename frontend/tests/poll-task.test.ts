import { describe, expect, it, vi } from "vitest";

import type { WorkflowTask } from "@/lib/types";
import { pollWorkflowTask } from "@/lib/workflow/poll-task";

function task(status: string): WorkflowTask {
  return {
    id: "task-1",
    task_type: "transcription",
    status,
    progress: status === "succeeded" ? 100 : 10,
    error_code: null,
    model_consent_event_id: null,
  };
}

describe("后台任务等待", () => {
  it("任务成功后停止轮询", async () => {
    const fetchTask = vi.fn()
      .mockResolvedValueOnce(task("processing"))
      .mockResolvedValueOnce(task("succeeded"));
    const result = await pollWorkflowTask(task("queued"), fetchTask, {
      sleep: async () => undefined,
    });
    expect(result.status).toBe("succeeded");
    expect(fetchTask).toHaveBeenCalledTimes(2);
  });

  it("页面暂时隐藏时不发起新请求", async () => {
    let pausedChecks = 0;
    const fetchTask = vi.fn().mockResolvedValue(task("succeeded"));
    await pollWorkflowTask(task("processing"), fetchTask, {
      isPaused: () => pausedChecks++ === 0,
      sleep: async () => undefined,
    });
    expect(fetchTask).toHaveBeenCalledTimes(1);
  });

  it("临时断网后退避重试并读取最终状态", async () => {
    const onTemporaryError = vi.fn();
    const onConnectionRestored = vi.fn();
    const fetchTask = vi.fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(task("succeeded"));
    const result = await pollWorkflowTask(task("running"), fetchTask, {
      sleep: async () => undefined,
      onTemporaryError,
      onConnectionRestored,
    });
    expect(result.status).toBe("succeeded");
    expect(onTemporaryError).toHaveBeenCalledWith(expect.any(Error), 1);
    expect(onConnectionRestored).toHaveBeenCalledOnce();
  });
});

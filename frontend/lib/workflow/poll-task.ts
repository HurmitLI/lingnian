import type { WorkflowTask } from "@/lib/types";

const TERMINAL_TASK_STATUSES = new Set(["succeeded", "failed_retryable", "failed_final"]);

type PollOptions = {
  maxWaitMs?: number;
  isPaused?: () => boolean;
  shouldRetryError?: (value: unknown) => boolean;
  onTemporaryError?: (value: unknown, attempt: number) => void;
  onConnectionRestored?: () => void;
  now?: () => number;
  sleep?: (milliseconds: number) => Promise<void>;
};

export async function pollWorkflowTask(
  initialTask: WorkflowTask,
  fetchTask: (taskId: string) => Promise<WorkflowTask>,
  options: PollOptions = {},
): Promise<WorkflowTask> {
  const maxWaitMs = options.maxWaitMs ?? 3 * 60 * 1000;
  const isPaused = options.isPaused ?? (() => false);
  const shouldRetryError = options.shouldRetryError ?? (() => true);
  const now = options.now ?? Date.now;
  const sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  }));
  const startedAt = now();
  let latest = initialTask;
  let interval = 750;
  let consecutiveFailures = 0;

  if (TERMINAL_TASK_STATUSES.has(latest.status)) return latest;

  while (now() - startedAt < maxWaitMs) {
    if (isPaused()) {
      await sleep(1000);
      continue;
    }
    try {
      latest = await fetchTask(latest.id);
      if (consecutiveFailures > 0) options.onConnectionRestored?.();
      consecutiveFailures = 0;
    } catch (value) {
      if (!shouldRetryError(value)) throw value;
      consecutiveFailures += 1;
      options.onTemporaryError?.(value, consecutiveFailures);
      await sleep(Math.min(1000 * (2 ** (consecutiveFailures - 1)), 5000));
      continue;
    }
    if (TERMINAL_TASK_STATUSES.has(latest.status)) return latest;
    await sleep(interval);
    interval = Math.min(Math.round(interval * 1.35), 3000);
  }

  throw new Error("处理时间比平时久。任务仍会在后台继续，请稍后刷新当前记录查看进度。");
}

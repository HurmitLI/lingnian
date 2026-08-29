const SESSION_STATUS_LABELS: Record<string, string> = {
  PROMPT_READY: "问题已准备",
  AUDIO_UPLOADED: "音频已保存",
  TRANSCRIBING: "正在本机转写",
  TRANSCRIPT_REVIEW: "等待家人校对",
  ORGANIZING: "正在整理故事",
  DRAFT_REVIEW: "等待家人确认",
  ARCHIVED: "已确认归档",
  SKIPPED: "已按意愿跳过",
  FAILED_RETRYABLE: "处理遇到问题，可以重试",
};

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: "等待开始",
  queued: "已经受理",
  running: "正在处理",
  succeeded: "处理完成",
  failed_retryable: "处理遇到问题，可以重试",
  failed_final: "处理未完成",
  cancelled: "已停止",
};

export function sessionStatusLabel(status: string): string {
  return SESSION_STATUS_LABELS[status] ?? "状态待确认，请刷新后再看";
}

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] ?? "状态待确认";
}

export function isSessionTerminal(status: string): boolean {
  return status === "ARCHIVED" || status === "SKIPPED";
}

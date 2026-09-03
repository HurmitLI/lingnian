const SESSION_STATUS_LABELS: Record<string, string> = {
  PROMPT_READY: "问题已准备",
  INTERVIEWING: "正在语音采访",
  RECORDING_PENDING: "等待留下声音",
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

const TASK_ERROR_MESSAGES: Record<string, string> = {
  INSUFFICIENT_STORY_CONTENT: "这段校对稿目前只有语气词或结束语，还不足以整理成真实故事。请先补充至少一句实际内容，例如“小时候我住在……，最记得……”，保存后再授权。系统不会为了进入下一步编造故事。",
  LLM_KEY_MISSING: "千问密钥尚未配置，暂时不能整理故事。",
  LLM_PROCESSING_FAILED: "录音和整场采访稿已经保存，只有故事草稿生成失败。请点击下方按钮重新生成，不需要重新采访。",
};

export function sessionStatusLabel(status: string): string {
  return SESSION_STATUS_LABELS[status] ?? "状态待确认，请刷新后再看";
}

export function taskStatusLabel(status: string): string {
  return TASK_STATUS_LABELS[status] ?? "状态待确认";
}

export function taskErrorMessage(errorCode: string | null): string {
  if (!errorCode) return "处理没有成功，请稍后再试。";
  return TASK_ERROR_MESSAGES[errorCode] ?? `处理没有成功（${errorCode}），请稍后再试。`;
}

export function hasMeaningfulStoryContent(value: string): boolean {
  const compact = value.normalize("NFKC").replace(/[\s，。！？、,.!?…~～—-]/g, "");
  const withoutFillers = compact.replace(/[啊阿呀哦噢喔嗯呃额诶哎唉哈呵哼嘛呢吧啦喽]/g, "");
  if (/^(没了|没有了|不知道|不记得|想不起来|不想说)*$/.test(withoutFillers)) return false;
  return withoutFillers.length >= 4;
}

export function isSessionTerminal(status: string): boolean {
  return status === "ARCHIVED" || status === "SKIPPED";
}

export function memoryWorkflowStep(status: string): number {
  if (["TRANSCRIPT_REVIEW", "ORGANIZING"].includes(status)) return 2;
  if (["DRAFT_REVIEW", "CONFIRMED", "ARCHIVED"].includes(status)) return 3;
  if (
    [
      "PROMPT_READY",
      "INTERVIEWING",
      "RECORDING_PENDING",
      "AUDIO_UPLOADED",
      "TRANSCRIBING",
      "FAILED_RETRYABLE",
    ].includes(status)
  ) {
    return 1;
  }
  return 0;
}

export type ArchiveSessionState = {
  label: string;
  description: string;
  action: string;
  tone: "recording" | "processing" | "review" | "attention";
};

export function archiveSessionState(status: string): ArchiveSessionState {
  if (status === "DRAFT_REVIEW") {
    return {
      label: "故事草稿待确认",
      description: "故事已经整理好，确认无误后就会进入正式档案。",
      action: "核对并归档",
      tone: "review",
    };
  }
  if (status === "TRANSCRIPT_REVIEW") {
    return {
      label: "采访稿已保存",
      description: "录音和文字都在，下一步生成故事草稿。",
      action: "继续生成故事",
      tone: "review",
    };
  }
  if (["TRANSCRIBING", "ORGANIZING"].includes(status)) {
    return {
      label: "正在自动整理",
      description: "任务仍在后台处理，可以稍后回来查看。",
      action: "查看进度",
      tone: "processing",
    };
  }
  if (status === "FAILED_RETRYABLE") {
    return {
      label: "需要继续处理",
      description: "内容已经保存，打开后可以从失败处继续。",
      action: "打开并处理",
      tone: "attention",
    };
  }
  return {
    label: "采访尚未完成",
    description: "已经留下的内容会保留，可以从上次停下的位置继续。",
    action: "继续采访",
    tone: "recording",
  };
}

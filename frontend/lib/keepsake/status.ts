import { IS_FORMAL_CLOUD } from "@/lib/runtime";

const KEEPSAKE_STATUS_LABELS: Record<string, string> = {
  queued: IS_FORMAL_CLOUD ? "已受理，等待开始合成" : "已受理，等待本机开始合成",
  rendering: IS_FORMAL_CLOUD ? "正在私密空间内合成视频" : "正在这台 Mac 上合成视频",
  ready: "原声视频已生成",
  failed_retryable: IS_FORMAL_CLOUD ? "视频合成遇到问题，可以重试" : "本机合成遇到问题，可以重试",
  failed_final: "本次合成未完成",
  corrupt: "视频完整性异常，已阻止播放",
};

const KEEPSAKE_ERROR_LABELS: Record<string, string> = {
  PROCESS_INTERRUPTED: "上次合成被中断，原始故事和录音都还在，可以重新开始。",
  SOURCE_ASSET_CORRUPT: "一份原始素材完整性异常，已停止合成以保护档案。",
  SOURCE_AUDIO_MISSING: "所选故事的原始录音已不可用，请重新选择。",
  KEEPSAKE_RENDER_FAILED: IS_FORMAL_CLOUD ? "视频合成没有完成，可以再试一次。" : "本机视频合成没有完成，可以再试一次。",
  FFMPEG_UNAVAILABLE: IS_FORMAL_CLOUD ? "视频组件暂时不可用，请稍后重试。" : "本机视频组件暂时不可用，请重新启动聆年后重试。",
};

export function keepsakeStatusLabel(status: string): string {
  return KEEPSAKE_STATUS_LABELS[status] ?? "状态待确认，请刷新后再看";
}

export function keepsakeErrorLabel(errorCode: string | null): string {
  if (!errorCode) return IS_FORMAL_CLOUD ? "视频合成没有完成，原始故事、录音和照片都不会被删除。" : "本机合成没有完成，原始故事、录音和照片都不会被删除。";
  return KEEPSAKE_ERROR_LABELS[errorCode] ?? (IS_FORMAL_CLOUD ? "视频合成没有完成，原始素材仍然安全保留。" : "本机合成没有完成，原始素材仍然安全保留。");
}

export function isKeepsakeTerminal(status: string): boolean {
  return ["ready", "failed_retryable", "failed_final", "corrupt"].includes(status);
}

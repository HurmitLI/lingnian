import type { GenerativeMediaRequest } from "@/lib/types";

const DURATION_RANGES: Record<string, readonly [number, number]> = {
  photo_restore: [3, 10],
  portrait_video: [12, 35],
  scene_video: [25, 70],
};

const ACTIVE_STATUSES = new Set(["queued", "processing"]);

function minuteRangeLabel(minMinutes: number, maxMinutes: number) {
  const min = Math.max(1, Math.ceil(minMinutes));
  const max = Math.max(min, Math.ceil(maxMinutes));
  return min === max ? `${min} 分钟` : `${min}–${max} 分钟`;
}

export function generationTimingLabel(request: GenerativeMediaRequest): string | null {
  if (!ACTIVE_STATUSES.has(request.status)) return null;
  const range = DURATION_RANGES[request.generation_type];
  if (!range) return "生成时间取决于当前工作流";
  const durationScale = request.generation_type === "scene_video"
    ? (request.production_spec?.target_duration_seconds ?? 60) / 60
    : 1;
  const scaledRange: readonly [number, number] = [range[0] * durationScale, range[1] * durationScale];
  if (request.status === "queued") {
    return `节点领取后通常约 ${minuteRangeLabel(scaledRange[0], scaledRange[1])}`;
  }
  const remainingRatio = Math.max(0.05, (100 - request.progress_percent) / 100);
  return `预计还需约 ${minuteRangeLabel(scaledRange[0] * remainingRatio, scaledRange[1] * remainingRatio)}`;
}

export function generationCompletionNotice(
  previousStatus: string | undefined,
  request: GenerativeMediaRequest,
): string | null {
  if (!previousStatus || !ACTIVE_STATUSES.has(previousStatus)) return null;
  if (request.status === "pending_human_review") return "成片已经生成完成，请播放并完成家庭验收。";
  if (request.status === "failed") return "成片生成没有完成，素材和故事仍在，可以直接重新制作。";
  return null;
}

export function storyPreviewSentences(body: string, limit = 3): string[] {
  const sentences = (body.match(/[^。！？!?\n]+[。！？!?]?/gu) ?? [])
    .map((item) => item.trim())
    .filter(Boolean);
  return (sentences.length ? sentences : [body.trim()]).filter(Boolean).slice(0, limit);
}

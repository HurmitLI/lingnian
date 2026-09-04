import { describe, expect, it } from "vitest";

import {
  generationCompletionNotice,
  generationTimingLabel,
  storyPreviewSentences,
} from "@/lib/generation/status";
import type { GenerativeMediaRequest } from "@/lib/types";

function request(overrides: Partial<GenerativeMediaRequest> = {}): GenerativeMediaRequest {
  return {
    id: "generation-1",
    elder_id: "elder-1",
    story_id: "story-1",
    result_asset_id: null,
    result_content_url: null,
    generation_type: "scene_video",
    provider_key: "home_comfyui",
    status: "processing",
    assigned_node_id: "node-1",
    actor_label: "家庭管理员",
    subject_consent: true,
    rights_confirmed: true,
    no_impersonation: true,
    allow_external_upload: true,
    estimated_cost_cents: 0,
    actual_cost_cents: 0,
    max_cost_cents: 100,
    production_spec: {},
    attempt_count: 1,
    progress_percent: 50,
    progress_stage: "正在制作分镜",
    progress_detail: {},
    result_report: {},
    queued_at: "2026-09-04T08:00:00Z",
    started_at: "2026-09-04T08:01:00Z",
    completed_at: null,
    last_error_message: null,
    error_code: null,
    review_checks: {},
    reviewed_by: null,
    review_notes: null,
    reviewed_at: null,
    created_at: "2026-09-04T08:00:00Z",
    ...overrides,
  };
}

describe("生成任务产品状态", () => {
  it("按制作类型和进度给出范围估计而不是精确承诺", () => {
    expect(generationTimingLabel(request())).toBe("预计还需约 13–35 分钟");
    expect(generationTimingLabel(request({ status: "queued" }))).toBe(
      "节点领取后通常约 25–70 分钟",
    );
    expect(generationTimingLabel(request({
      status: "queued",
      production_spec: { target_duration_seconds: 90 },
    }))).toBe("节点领取后通常约 38–105 分钟");
    expect(generationTimingLabel(request({ status: "accepted" }))).toBeNull();
  });

  it("只在后台任务从运行态进入结果态时提醒", () => {
    expect(generationCompletionNotice("processing", request({ status: "pending_human_review" })))
      .toContain("生成完成");
    expect(generationCompletionNotice("queued", request({ status: "failed" })))
      .toContain("重新制作");
    expect(generationCompletionNotice(undefined, request({ status: "failed" }))).toBeNull();
  });

  it("立即从已确认故事提取可复核的分镜原文", () => {
    expect(storyPreviewSentences("第一句。第二句！第三句？第四句。"))
      .toEqual(["第一句。", "第二句！", "第三句？"]);
  });
});

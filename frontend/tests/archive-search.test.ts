import { describe, expect, it } from "vitest";

import { matchesArchiveSearch } from "@/lib/memory/archive-search";
import type { TimelineItem } from "@/lib/types";

const story: TimelineItem = {
  story: { id: "story-1", title: "第一次坐火车", body: "我从县城去了上海。", confirmed_by: "女儿", confirmed_at: "2026-09-08T00:00:00Z" },
  source_session_id: "session-1",
  interview_mode: "guided_voice",
  interview_turn_count: 6,
  life_stage: "求学",
  narrator_person_id: "person-1",
  narrator_label: "雯儿",
  narration_kind: "first_person",
  events: [{ id: "event-1", time_expression: "十八岁那年", normalized_time: "1978", confidence: "confirmed" }],
  audio_url: "/audio.wav",
  image_url: null,
  image_asset_id: null,
  image_annotation: null,
  detail: { id: "detail-1", story_id: "story-1", place_name: "上海站", event_year: 1978, theme_tags: ["求学", "迁居"], summary: "一次难忘的远行", updated_by: "女儿", updated_at: "2026-09-08T00:00:00Z" },
  contributions: [{ id: "c-1", story_id: "story-1", contributor_person_id: null, contributor_label: "小姨", contribution_type: "alternate_memory", body: "我记得是从苏州转车。", status: "disputed", created_at: "2026-09-08T00:00:00Z" }],
  person_tags: [{ id: "tag-1", family_id: "family-1", media_asset_id: "asset-1", person_id: "person-2", person_name: "外婆", tagged_by: "女儿", note: "站在左边", created_at: "2026-09-08T00:00:00Z" }],
};

describe("家庭档案统一搜索", () => {
  it.each(["雯儿", "上海站", "1978", "迁居", "苏州转车", "外婆"])("可以按 %s 找到故事", (query) => {
    expect(matchesArchiveSearch(story, query)).toBe(true);
  });

  it("忽略首尾空格和大小写", () => {
    expect(matchesArchiveSearch(story, "  STORY  ")).toBe(false);
    expect(matchesArchiveSearch(story, "  上 海  ")).toBe(false);
    expect(matchesArchiveSearch(story, "  上海  ")).toBe(true);
  });
});

import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const profile = {
  id: "elder-test",
  person_id: "person-elder-test",
  family_id: "family-test",
  data_classification: "authorized_sensitive",
  display_name: "测试奶奶",
  preferred_name: "奶奶",
  birth_year: 1948,
  birth_era: null,
  native_place: null,
  occupation_summary: null,
};

const session = {
  id: "session-test",
  elder_id: profile.id,
  narrator_person_id: profile.person_id,
  interview_mode: "single",
  life_stage: "童年",
  prompt_id: "prompt-test",
  question_text: "小时候，哪件小事让你一直记到现在？",
  status: "PROMPT_READY",
  created_at: "2026-08-29T08:00:00Z",
  updated_at: "2026-08-29T08:00:00Z",
};

const gapSession = {
  ...session,
  id: "session-family-question",
  life_stage: "家人提问",
  prompt_id: "family-question:test",
  question_text: "年轻时第一次离开家乡是什么时候？",
};

const guidedSession = {
  ...session,
  id: "session-guided",
  narrator_person_id: "person-test",
  interview_mode: "guided_voice",
  question_text: "你第一次听家里人讲起外公，是在什么时候？",
  status: "INTERVIEWING",
};

const archivedSession = {
  ...session,
  id: "session-archived",
  narrator_person_id: "person-test",
  interview_mode: "guided_voice",
  status: "ARCHIVED",
};

const archivedTurns = [
  {
    id: "archived-turn-1",
    session_id: archivedSession.id,
    turn_index: 1,
    question_text: "那时候你最常和谁一起去河边？",
    raw_answer_text: "我常和姐姐一起去。",
    corrected_answer_text: "我常和姐姐一起去。",
    answer_version: 1,
    audio_asset_id: "archived-audio-1",
    audio_url: null,
    question_audio_asset_id: "archived-question-audio-1",
    question_audio_url: null,
    asr_provider: "mock",
    asr_model: "mock",
    followup_mode: "local_private",
    status: "complete",
  },
  {
    id: "archived-turn-2",
    session_id: archivedSession.id,
    turn_index: 2,
    question_text: "那段回忆里，你最记得什么？",
    raw_answer_text: "最记得夏天河边的风。",
    corrected_answer_text: "最记得夏天河边的风。",
    answer_version: 1,
    audio_asset_id: "archived-audio-2",
    audio_url: null,
    question_audio_asset_id: "archived-question-audio-2",
    question_audio_url: null,
    asr_provider: "mock",
    asr_model: "mock",
    followup_mode: "local_private",
    status: "complete",
  },
];

const timelineItem = {
  story: {
    id: "story-test",
    title: "河边的夏天",
    body: "小时候常跟家里人去河边，风吹过来的时候很凉快。",
    confirmed_by: "测试家人",
    confirmed_at: "2026-08-29T09:00:00Z",
  },
  source_session_id: archivedSession.id,
  interview_mode: "guided_voice",
  interview_turn_count: archivedTurns.length,
  life_stage: "童年",
  narrator_person_id: "person-test",
  narrator_label: "测试女儿",
  narration_kind: "family_recollection",
  events: [],
  audio_url: null,
  image_url: null,
  image_asset_id: null,
  image_annotation: null,
  detail: {
    id: "detail-test",
    story_id: "story-test",
    place_name: "测试河边",
    event_year: 1960,
    theme_tags: ["童年", "夏天"],
    summary: "一段发生在河边的虚构童年故事。",
    updated_by: "测试家人",
    updated_at: "2026-08-29T09:00:00Z",
  },
  contributions: [{
    id: "contribution-test",
    story_id: "story-test",
    contributor_person_id: "person-test",
    contributor_label: "测试女儿",
    contribution_type: "context",
    body: "家里还保存着一张当时的车票。",
    status: "open",
    created_at: "2026-08-29T10:00:00Z",
  }],
  person_tags: [],
};

const unexpectedBrowserErrors = new WeakMap<Page, string[]>();

async function mockLocalApi(page: Page) {
  let guidedTurns: Array<Record<string, unknown>> = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = [];

    if (path === "/api/v1/health") {
      body = { status: "ok", environment: "e2e", asr_provider: "local", llm_provider: "qwen" };
    } else if (path === "/api/v1/elder-profiles") {
      body = [profile];
    } else if (path.endsWith("/timeline")) {
      body = [timelineItem];
    } else if (path.endsWith("/memory-context")) {
      body = { coverage: [], preferences: [], confirmed_facts: [] };
    } else if (path.endsWith("/reminders") || path.endsWith("/memory-books")) {
      body = [];
    } else if (path.endsWith("/memory-sessions")) {
      body = [session, gapSession];
    } else if (path.endsWith("/people")) {
      body = [
        { id: profile.person_id, family_id: profile.family_id, role: "elder", display_name: "测试奶奶", created_at: "2026-08-29T08:00:00Z" },
        { id: "person-test", family_id: profile.family_id, role: "family_member", display_name: "测试女儿", created_at: "2026-08-29T08:00:00Z" },
      ];
    } else if (path.endsWith("/legacy-plan")) {
      body = null;
    } else if (path === "/api/v1/generative-media/capabilities") {
      body = [{ generation_type: "portrait_video", label: "人物讲述视频", available: false, provider_key: null, requires_external_upload: true, requires_subject_consent: true, estimated_cost_cents: null, unavailable_reason: "尚未配置付费服务。" }];
    } else if (path.endsWith("/documentary-plan-preview")) {
      body = {
        format: "lingnian-documentary-storyboard",
        version: 2,
        production_spec: { target_duration_seconds: 60, aspect_ratio: "16:9" },
        audio_plan: { strategy: "original_recording_first", exact_story_alignment_required: true, synthetic_voice_allowed: false, fallback: "保留字幕" },
        scenes: [
          { scene: 1, kind: "title_card", duration_seconds: 4, narration: "", subtitle: timelineItem.story.title, source: "人工确认故事标题", visual_direction: "标题卡" },
          { scene: 2, kind: "documentary_context", duration_seconds: 13, narration: timelineItem.story.body, subtitle: timelineItem.story.body, source: "人工确认故事原文", visual_direction: "纪实空镜" },
          { scene: 3, kind: "documentary_context", duration_seconds: 13, narration: timelineItem.story.body, subtitle: timelineItem.story.body, source: "人工确认故事原文", visual_direction: "纪实空镜" },
          { scene: 4, kind: "documentary_context", duration_seconds: 12, narration: timelineItem.story.body, subtitle: timelineItem.story.body, source: "人工确认故事原文", visual_direction: "纪实空镜" },
          { scene: 5, kind: "documentary_context", duration_seconds: 13, narration: timelineItem.story.body, subtitle: timelineItem.story.body, source: "人工确认故事原文", visual_direction: "纪实空镜" },
          { scene: 6, kind: "source_card", duration_seconds: 5, narration: "", subtitle: "这段记忆来自家人确认的口述与家庭档案", source: "聆年档案来源说明", visual_direction: "来源卡" },
        ],
        review_checklist: ["声音完整", "镜头连贯", "没有新增家庭事实"],
      };
    } else if (path.endsWith("/generative-media-requests")) {
      body = [];
    } else if (path.endsWith("/archive-questions")) {
      body = {
        question: "小时候常去哪里？",
        status: "grounded",
        answer: "在已确认的家庭档案里，最相关的是《河边的夏天》。",
        citations: [{ source_id: timelineItem.story.id, story_id: timelineItem.story.id, source_kind: "elder_story", source_label: "奶奶", title: timelineItem.story.title, life_stage: "童年", excerpt: timelineItem.story.body, audio_url: null, image_url: null, score: 0.9 }],
        follow_up_question: null,
        answer_mode: "local_extract_with_sources",
      };
    } else if (path === `/api/v1/memory-sessions/${session.id}`) {
      body = { session, media_assets: [], interview_turns: [], transcript: null, story_draft: null, tasks: [] };
    } else if (path === `/api/v1/memory-sessions/${guidedSession.id}`) {
      body = { session: guidedSession, media_assets: [], interview_turns: guidedTurns, transcript: null, story_draft: null, tasks: [] };
    } else if (path === `/api/v1/memory-sessions/${archivedSession.id}`) {
      body = { session: archivedSession, media_assets: [], interview_turns: archivedTurns, transcript: null, story_draft: null, tasks: [] };
    } else if (path === `/api/v1/memory-sessions/${guidedSession.id}/interview-turns/audio`) {
      body = {
        id: "guided-turn-uploaded",
        session_id: guidedSession.id,
        turn_index: 1,
        question_text: guidedSession.question_text,
        raw_answer_text: "我第一次听妈妈提起外公。",
        corrected_answer_text: "我第一次听妈妈提起外公。",
        answer_version: 1,
        audio_asset_id: "guided-audio",
        audio_url: "/api/v1/media-assets/guided-audio/content",
        question_audio_asset_id: "guided-question-audio",
        question_audio_url: "/api/v1/media-assets/guided-question-audio/content",
        asr_provider: "mock",
        asr_model: "mock",
        followup_mode: "pending",
        status: "answer_review",
      };
    } else if (path.includes(`/api/v1/memory-sessions/${guidedSession.id}/interview-turns/`) && path.endsWith("/continue")) {
      guidedTurns = Array.from({ length: 12 }, (_, index) => ({
        id: `guided-turn-${index + 1}`,
        session_id: guidedSession.id,
        turn_index: index + 1,
        question_text: index === 0 ? guidedSession.question_text : `第 ${index + 1} 个测试问题`,
        raw_answer_text: "测试回答。",
        corrected_answer_text: "测试回答。",
        answer_version: 2,
        audio_asset_id: `guided-audio-${index + 1}`,
        audio_url: null,
        question_audio_asset_id: `guided-question-audio-${index + 1}`,
        question_audio_url: null,
        asr_provider: "mock",
        asr_model: "mock",
        followup_mode: "local_private",
        status: "complete",
      }));
      body = {
        session: guidedSession,
        turn: guidedTurns[0],
        acknowledgement: "谢谢你慢慢讲。",
        next_question: "后来你又听说了什么？",
        should_end: true,
        followup_mode: "local_private",
      };
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  unexpectedBrowserErrors.set(page, errors);
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  await mockLocalApi(page);
});

test.afterEach(async ({ page }) => {
  const errors = unexpectedBrowserErrors.get(page) ?? [];
  expect(errors, errors.join("\n")).toEqual([]);
});

test("首页可导航且没有横向溢出", async ({ page, isMobile }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "今天，陪奶奶聊一点" })).toBeVisible();
  if (!isMobile) {
    await expect(page.getByRole("link", { name: "聆年首页" }).locator("img")).toHaveAttribute("src", /lingnian-mark-v3/);
  }
  await expect(page.locator(".welcome-card a.button.primary")).toHaveCount(1);
  const navigationName = isMobile ? "手机主导航" : "桌面主导航";
  const navigation = page.getByRole("navigation", { name: navigationName });
  await expect(navigation).toBeVisible();
  await navigation.getByRole("link", { name: "回忆档案" }).click();
  await expect(page).toHaveURL(/\/archive$/);
  await expect(page.getByRole("heading", { level: 1, name: "奶奶的回忆" })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "回忆档案" })).toHaveAttribute("aria-current", "page");

  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations, accessibility.violations.map((item) => `${item.id}: ${item.help}`).join("\n")).toEqual([]);
});

test("刷新后能从链接恢复未完成记录", async ({ page }) => {
  await page.goto(`/record?elder=${profile.id}&session=${session.id}`);
  await expect(page.getByRole("heading", { level: 1, name: "一次，只聊一个回忆" })).toBeVisible();
  await expect(page.getByText(session.question_text)).toBeVisible();
  await expect(page.getByText("问题已准备")).toBeVisible();
  const workflow = page.getByRole("list", { name: "记录回忆的四个步骤" });
  await expect(workflow).toBeVisible();
  await expect(workflow.locator('[aria-current="step"]')).toContainText("留下声音");
  await page.reload();
  await expect(page.getByText(session.question_text)).toBeVisible();
});

test("语音采访明确区分回忆对象与讲述人", async ({ page }) => {
  await page.goto(`/record?elder=${profile.id}&session=${guidedSession.id}`);
  await expect(page.getByText("回忆对象")).toBeVisible();
  await expect(page.getByText("本次讲述人")).toBeVisible();
  await expect(page.getByText("测试女儿", { exact: true })).toBeVisible();
  await expect(page.getByText(guidedSession.question_text)).toBeVisible();
  await expect(page.getByText(/自动整理已开启/)).toBeVisible();
  await expect(page.getByRole("button", { name: /开始连续采访/ })).toBeVisible();
  await expect(page.getByText("一次开始，后面只管慢慢讲")).toBeVisible();
  await expect(page.getByRole("button", { name: /再听一遍/ })).toBeVisible();
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations, accessibility.violations.map((item) => `${item.id}: ${item.help}`).join("\n")).toEqual([]);
});

test("连续采访一次开始后自动进入收音和提交", async ({ page }) => {
  await page.addInitScript(() => {
    const track = {
      addEventListener: () => undefined,
      stop: () => undefined,
    } as unknown as MediaStreamTrack;
    const stream = {
      getAudioTracks: () => [track],
      getTracks: () => [track],
    } as unknown as MediaStream;

    class FakeMediaRecorder {
      static isTypeSupported() { return true; }
      state: RecordingState = "inactive";
      mimeType = "audio/webm";
      ondataavailable: ((event: BlobEvent) => void) | null = null;
      onstop: (() => void) | null = null;
      onerror: (() => void) | null = null;
      start() { this.state = "recording"; }
      stop() {
        if (this.state !== "recording") return;
        this.state = "inactive";
        this.ondataavailable?.({ data: new Blob(["voice"], { type: this.mimeType }) } as BlobEvent);
        this.onstop?.();
      }
    }

    class FakeAudio {
      onended: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      pause() { return undefined; }
      play() {
        window.setTimeout(() => this.onended?.(new Event("ended")), 20);
        return Promise.resolve();
      }
    }

    class FakeAudioContext {
      createAnalyser() {
        return {
          fftSize: 2048,
          smoothingTimeConstant: 0,
          getFloatTimeDomainData: (samples: Float32Array) => samples.fill(0),
        } as unknown as AnalyserNode;
      }
      createMediaStreamSource() {
        return { connect: () => undefined, disconnect: () => undefined } as unknown as MediaStreamAudioSourceNode;
      }
      close() { return Promise.resolve(); }
    }

    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: () => Promise.resolve(stream) },
    });
    Object.defineProperty(window, "MediaRecorder", { configurable: true, value: FakeMediaRecorder });
    Object.defineProperty(window, "Audio", { configurable: true, value: FakeAudio });
    Object.defineProperty(window, "AudioContext", { configurable: true, value: FakeAudioContext });
  });

  await page.goto(`/record?elder=${profile.id}&session=${guidedSession.id}`);
  await page.getByRole("button", { name: "开始连续采访" }).click();
  await expect(page.getByText("可以开始说了")).toBeVisible();
  await expect(page.getByRole("button", { name: "我还在想" })).toBeVisible();
  await page.getByRole("button", { name: "我还在想" }).click();
  await expect(page.getByText(/接下来 20 秒不会/)).toBeVisible();
  await page.getByRole("button", { name: "我说完了" }).click();
  await expect(page.getByText(/已经聊满 12 轮/)).toBeVisible();
  await expect(page.getByRole("button", { name: "结束这次采访，查看完整整理稿" })).toBeVisible();
});

test("回忆档案可以搜索并清除筛选", async ({ page }) => {
  await page.goto("/archive");
  await expect(page.getByRole("heading", { name: timelineItem.story.title })).toBeVisible();
  const search = page.getByRole("searchbox", { name: "搜索故事" });
  await search.fill("找不到的内容");
  await expect(page.getByRole("heading", { name: "没有找到符合条件的故事" })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.getByRole("heading", { name: timelineItem.story.title })).toBeVisible();
});

test("回忆档案能找回未完成记录并展开完整采访", async ({ page }) => {
  await page.goto("/archive");
  await expect(page.getByText("2 条待完成", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "继续采访" }).first()).toBeVisible();
  await page.getByRole("button", { name: "查看完整采访 · 2轮" }).click();
  await expect(page.getByText("那时候你最常和谁一起去河边？")).toBeVisible();
  await expect(page.getByText("最记得夏天河边的风。")).toBeVisible();
  await expect(page.getByText("为这篇故事补一张照片（可选）")).toBeVisible();
});

test("家族记忆可以溯源回答并浏览人生轨迹", async ({ page }) => {
  await page.goto("/memory");
  await expect(page.getByRole("heading", { level: 1, name: "让后来的人，不只看到一份文件" })).toBeVisible();
  await expect(page.getByRole("link", { name: /年轻时第一次离开家乡/ })).toBeVisible();
  await page.getByLabel("你想知道什么？").fill("小时候常去哪里？");
  await page.getByRole("button", { name: "从家庭档案里找答案" }).click();
  await expect(page.getByText("来自已确认档案")).toBeVisible();
  await expect(page.getByRole("heading", { name: timelineItem.story.title })).toBeVisible();
  await page.getByRole("button", { name: "人生轨迹" }).click();
  await expect(page.getByText("测试河边")).toBeVisible();
  await page.getByRole("button", { name: "家人补充" }).click();
  await expect(page.getByText("家里还保存着一张当时的车票。")).toBeVisible();
  await page.getByRole("button", { name: "确认这条补充" }).click();
  await expect(page.getByText("家人核对状态已经保存。")).toBeVisible();
  await page.getByRole("button", { name: "影像实验室" }).click();
  await expect(page.getByRole("heading", { name: "先把修复或生成需要的材料整理好" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载制作包" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "提交后可以离开，完成后再回来看片" })).toBeVisible();
  await expect(page.getByLabel("本次成片内容预览")).toContainText(timelineItem.story.title);
  await expect(page.getByLabel("影片制作规格")).toContainText("默认使用原声，不克隆声音");
  await expect(page.getByLabel("影片制作规格").getByLabel("目标时长")).toHaveValue("60");
  await expect(page.getByLabel("纪实影片分镜预览")).toContainText("6 个镜头 · 共 60 秒");
  await expect(page.getByLabel("纪实影片分镜预览")).toContainText("聆年档案来源说明");
  await expect(page.getByText("未验收不发布")).toBeVisible();
  await expect(page.getByRole("button", { name: "登记成片验收任务" })).toBeVisible();
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations, accessibility.violations.map((item) => `${item.id}: ${item.help}`).join("\n")).toEqual([]);
});

test("平板和宽屏断点保持可用", async ({ page, isMobile }) => {
  test.skip(isMobile, "桌面项目覆盖可调整视口的 768 和 1440 断点");
  for (const width of [768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/family");
    await expect(page.getByRole("heading", { level: 1, name: "把人物、意愿和安全设置好" })).toBeVisible();
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      minButtonHeight: Math.min(...Array.from(document.querySelectorAll("button")).map((button) => button.getBoundingClientRect().height)),
    }));
    expect(layout.overflow).toBe(false);
    expect(layout.minButtonHeight).toBeGreaterThanOrEqual(48);
  }
});

test("手机记录页一次只突出一个开始动作", async ({ page, isMobile }) => {
  test.skip(!isMobile, "手机项目覆盖简化后的记录入口");
  await page.goto("/record");
  await expect(page.getByLabel("选择一个话题")).toBeVisible();
  await expect(page.getByRole("button", { name: "开始语音采访" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "用照片或老物件触发回忆" })).toBeHidden();
  await expect(page.getByRole("button", { name: /童年/ })).toBeHidden();
});

test("手机档案先看故事，低频工具默认收起", async ({ page, isMobile }) => {
  test.skip(!isMobile, "手机项目覆盖简化后的档案页");
  await page.goto("/archive");
  const storyHeading = page.getByRole("heading", { level: 2, name: "奶奶的回忆档案" });
  const toolsHeading = page.getByRole("heading", { name: "保存与维护" });
  await expect(storyHeading).toBeVisible();
  await expect(toolsHeading).toBeVisible();
  expect((await storyHeading.boundingBox())?.y).toBeLessThan((await toolsHeading.boundingBox())?.y ?? 0);
  await expect(page.getByText("展开提醒与回忆录工具", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "添加应用内提醒" })).toBeHidden();
});

test("手机家庭管理去掉重复选择，话题设置默认收起", async ({ page, isMobile }) => {
  test.skip(!isMobile, "手机项目覆盖简化后的家庭管理页");
  await page.goto("/family");
  await expect(page.getByLabel("当前人物档案")).toHaveCount(1);
  await expect(page.getByText("查看或修改 7 个话题意愿", { exact: true })).toBeVisible();
  await expect(page.getByText("童年", { exact: true })).toBeHidden();
});

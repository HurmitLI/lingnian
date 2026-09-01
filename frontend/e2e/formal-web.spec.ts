import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const profile = {
  id: "elder-test",
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
  life_stage: "童年",
  prompt_id: "prompt-test",
  question_text: "小时候，哪件小事让你一直记到现在？",
  status: "PROMPT_READY",
  created_at: "2026-08-29T08:00:00Z",
  updated_at: "2026-08-29T08:00:00Z",
};

const timelineItem = {
  story: {
    id: "story-test",
    title: "河边的夏天",
    body: "小时候常跟家里人去河边，风吹过来的时候很凉快。",
    confirmed_by: "测试家人",
    confirmed_at: "2026-08-29T09:00:00Z",
  },
  life_stage: "童年",
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
  contributions: [],
  person_tags: [],
};

const unexpectedBrowserErrors = new WeakMap<Page, string[]>();

async function mockLocalApi(page: Page) {
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
      body = [session];
    } else if (path.endsWith("/people")) {
      body = [{ id: "person-test", family_id: profile.family_id, role: "family_member", display_name: "测试女儿", created_at: "2026-08-29T08:00:00Z" }];
    } else if (path.endsWith("/legacy-plan")) {
      body = null;
    } else if (path === "/api/v1/generative-media/capabilities") {
      body = [{ generation_type: "portrait_video", label: "人物讲述视频", available: false, provider_key: null, requires_external_upload: true, requires_subject_consent: true, estimated_cost_cents: null, unavailable_reason: "尚未配置付费服务。" }];
    } else if (path.endsWith("/generative-media-requests")) {
      body = [];
    } else if (path.endsWith("/archive-questions")) {
      body = {
        question: "小时候常去哪里？",
        status: "grounded",
        answer: "在已确认的家庭档案里，最相关的是《河边的夏天》。",
        citations: [{ story_id: timelineItem.story.id, title: timelineItem.story.title, life_stage: "童年", excerpt: timelineItem.story.body, audio_url: null, image_url: null, score: 0.9 }],
        follow_up_question: null,
        answer_mode: "local_extract_with_sources",
      };
    } else if (path === `/api/v1/memory-sessions/${session.id}`) {
      body = { session, media_assets: [], transcript: null, story_draft: null, tasks: [] };
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
  await expect(page.getByRole("heading", { level: 1, name: "奶奶的故事" })).toBeVisible();
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

test("回忆档案可以搜索并清除筛选", async ({ page }) => {
  await page.goto("/archive");
  await expect(page.getByRole("heading", { name: timelineItem.story.title })).toBeVisible();
  const search = page.getByRole("searchbox", { name: "搜索故事" });
  await search.fill("找不到的内容");
  await expect(page.getByRole("heading", { name: "没有找到符合条件的故事" })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.getByRole("heading", { name: timelineItem.story.title })).toBeVisible();
});

test("家族记忆可以溯源回答并浏览人生轨迹", async ({ page }) => {
  await page.goto("/memory");
  await expect(page.getByRole("heading", { level: 1, name: "让后来的人，不只看到一份文件" })).toBeVisible();
  await page.getByLabel("你想知道什么？").fill("小时候常去哪里？");
  await page.getByRole("button", { name: "从家庭档案里找答案" }).click();
  await expect(page.getByText("来自已确认档案")).toBeVisible();
  await expect(page.getByRole("heading", { name: timelineItem.story.title })).toBeVisible();
  await page.getByRole("button", { name: "人生轨迹" }).click();
  await expect(page.getByText("测试河边")).toBeVisible();
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
  await expect(page.getByRole("button", { name: "准备一个问题" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "用照片或老物件触发回忆" })).toBeHidden();
  await expect(page.getByRole("button", { name: /童年/ })).toBeHidden();
});

test("手机档案先看故事，低频工具默认收起", async ({ page, isMobile }) => {
  test.skip(!isMobile, "手机项目覆盖简化后的档案页");
  await page.goto("/archive");
  const storyHeading = page.getByRole("heading", { level: 2, name: "奶奶的故事" });
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
  await expect(page.getByLabel("当前讲述者")).toHaveCount(1);
  await expect(page.getByText("查看或修改 7 个话题意愿", { exact: true })).toBeVisible();
  await expect(page.getByText("童年", { exact: true })).toBeHidden();
});

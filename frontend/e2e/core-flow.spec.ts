import { expect, test } from "@playwright/test";

function silentWavBuffer(seconds = 0.15): Buffer {
  const sampleRate = 16_000;
  const dataLength = Math.round(sampleRate * seconds) * 2;
  const buffer = Buffer.alloc(44 + dataLength);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataLength, 4);
  buffer.write("WAVE", 8);
  buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataLength, 40);
  return buffer;
}

test("隔离数据完成记录、校对、整理和归档", async ({ page, isMobile }) => {
  test.setTimeout(90_000);
  const suffix = `${isMobile ? "手机" : "桌面"}-${Date.now()}`;
  const preferredName = `测试长辈${suffix}`;
  const correctedStory = `${preferredName}小时候常跟家里人去河边。这是第三阶段隔离验收内容。`;

  await page.goto("/family");
  await expect(page.getByRole("heading", { name: "人物档案" })).toBeVisible();
  const addNarrator = page.getByText("添加一位家人档案", { exact: true });
  if ((await addNarrator.count()) > 0) {
    await addNarrator.click();
  }
  await expect(
    page.getByRole("button", { name: /(建立人物档案|添加并切换到此人)/ }),
  ).toBeVisible();
  const familyNameInput = page.getByLabel("家庭档案名称");
  if ((await familyNameInput.count()) > 0) {
    await familyNameInput.fill(`隔离家庭${suffix}`);
  }
  await page.getByLabel("这位家人的显示名称").fill(`${preferredName}（虚构）`);
  await page.getByLabel("家里怎么称呼这位家人").fill(preferredName);
  await page.getByRole("button", { name: /(建立人物档案|添加并切换到此人)/ }).click();
  await expect(page.getByRole("status").filter({ hasText: /(人物档案已建立|新人物已添加)/ })).toBeVisible();

  const navigation = page.getByRole("navigation", { name: isMobile ? "手机主导航" : "桌面主导航" });
  await navigation.getByRole("link", { name: "开始记录" }).click();
  if (isMobile) {
    await page.getByLabel("选择一个话题").selectOption("童年");
    await page.getByRole("button", { name: "开始语音采访" }).click();
  } else {
    await page.getByRole("button", { name: /童年/ }).click();
  }
  await expect(page.getByText(/正在采访 · 第 1 轮/)).toBeVisible();

  const audioInput = page.locator('input[aria-label="选择已有音频"]');
  await audioInput.setInputFiles({
    name: "第三阶段测试.wav",
    mimeType: "audio/wav",
    buffer: silentWavBuffer(),
  });
  await expect(page.getByText("第三阶段测试.wav", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存回答，自动继续" }).click();
  await expect(page.getByText(/正在采访 · 第 2 轮/)).toBeVisible();
  await page.getByRole("button", { name: "结束这次采访，查看完整整理稿" }).click();

  const correctedInput = page.getByRole("textbox", { name: "人工校对稿" });
  await expect(correctedInput).toBeVisible();
  await correctedInput.fill(correctedStory);
  await page.getByRole("button", { name: "保存校对稿" }).click();
  await page.getByRole("button", { name: "按原话整理故事" }).click();
  const confirmedTitle = `河边的回忆 · ${suffix}`;
  const confirmedStory = `${correctedStory} 家人确认：那天的风很凉。`;
  await page.getByRole("textbox", { name: "确认后的故事标题" }).fill(confirmedTitle);
  await page.getByRole("textbox", { name: "确认后的故事正文" }).fill(confirmedStory);
  await page.getByRole("button", { name: "确认这个版本并归档" }).click();
  await expect(page).toHaveURL(/\/archive\?elder=[0-9a-f-]+$/);
  await expect(page.getByRole("heading", { name: confirmedTitle })).toBeVisible();
  await expect(page.getByRole("paragraph").filter({ hasText: confirmedStory })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);

  await page.getByRole("link", { name: "快速影像导出" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "把亲口讲过的故事，留成一段视频" })).toBeVisible();
  const createButton = page.getByRole("button", { name: "确认授权并开始本机制作" });
  await expect(createButton).toBeDisabled();
  await page.getByLabel(new RegExp(confirmedTitle)).check();
  await page.getByLabel("我确认有权使用这次所选的原始录音。").check();
  await page.getByLabel("这份视频只用于家庭记忆保存。").check();
  await page.getByLabel("不会把视频用于仿冒、误导或冒充本人。").check();
  await page.getByLabel("本次只使用亲口说过的原始声音，不生成新语音。").check();
  await expect(createButton).toBeEnabled();
  await createButton.click();
  await expect(page.locator("video")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/第 1 版 · 原声视频已生成/)).toBeVisible();
  await expect(page.getByText("家庭记忆整理 · 原始录音 · 非实时影像", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/keepsake=.*elder=|elder=.*keepsake=/);
  await page.reload();
  await expect(page.getByText(/第 1 版 · 原声视频已生成/)).toBeVisible();
  await expect(page.locator("video")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
});

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
  test.setTimeout(60_000);
  const suffix = `${isMobile ? "手机" : "桌面"}-${Date.now()}`;
  const preferredName = `测试长辈${suffix}`;
  const correctedStory = `${preferredName}小时候常跟家里人去河边。这是第三阶段隔离验收内容。`;

  await page.goto("/family");
  const familyNameInput = page.getByLabel("虚构家庭名称");
  if (!(await familyNameInput.isVisible())) {
    await page.getByText("再建一个测试档案", { exact: true }).click();
  }
  await familyNameInput.fill(`隔离家庭${suffix}`);
  await page.getByLabel("档案显示名").fill(`${preferredName}（虚构）`);
  await page.getByLabel("希望怎么称呼").fill(preferredName);
  await page.getByRole("button", { name: "保存测试档案" }).click();
  await expect(page.getByRole("status").filter({ hasText: "虚构测试档案已保存" })).toBeVisible();

  const navigation = page.getByRole("navigation", { name: isMobile ? "手机主导航" : "桌面主导航" });
  await navigation.getByRole("link", { name: "开始记录" }).click();
  if (isMobile) {
    await page.getByLabel("选择一个话题").selectOption("童年");
    await page.getByRole("button", { name: "准备一个问题" }).click();
  } else {
    await page.getByRole("button", { name: /童年/ }).click();
  }
  await expect(page.getByText("问题已准备", { exact: true })).toBeVisible();

  const audioInput = page.locator('input[aria-label="选择已有音频"]');
  await audioInput.setInputFiles({
    name: "第三阶段测试.wav",
    mimeType: "audio/wav",
    buffer: silentWavBuffer(),
  });
  await expect(page.getByText("第三阶段测试.wav", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认上传到本机档案" }).click();
  await expect(page.getByText(/已保留原始音频：第三阶段测试.wav/)).toBeVisible();
  await page.getByRole("button", { name: "开始本地转写" }).click();

  const correctedInput = page.getByRole("textbox", { name: "人工校对稿" });
  await expect(correctedInput).toBeVisible();
  await correctedInput.fill(correctedStory);
  await page.getByRole("button", { name: "保存校对稿" }).click();
  await page.getByRole("button", { name: "按原话整理故事" }).click();
  await expect(page.getByRole("heading", { name: "一段愿意留给家人的回忆" })).toBeVisible();
  await expect(page.locator("p.story-body")).toHaveText(correctedStory);
  await page.getByRole("button", { name: "人工确认并归档" }).click();
  await expect(page.getByText("这段故事已由人工确认并归档，可以到“回忆档案”查看。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "今天想从哪一段聊起？" })).toBeVisible();

  await navigation.getByRole("link", { name: "回忆档案" }).click();
  await expect(page.getByRole("heading", { name: "一段愿意留给家人的回忆" })).toBeVisible();
  await expect(page.getByRole("paragraph").filter({ hasText: correctedStory })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
});

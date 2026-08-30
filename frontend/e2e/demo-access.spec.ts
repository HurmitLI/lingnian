import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const inviteCode = process.env.DEMO_E2E_INVITE_CODE ?? "LINGNIAN-E2E-2026";

test("未登录时页面和媒体均被隔离，邀请码登录后可只读浏览", async ({ page, context }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/demo-login$/);
  await expect(page.getByRole("heading", { name: "打开沈家的回忆" })).toBeVisible();

  const blockedMedia = await page.request.get("/showcase/shen-suqin-story.mp4");
  expect(blockedMedia.status()).toBe(401);
  expect(blockedMedia.headers()["cache-control"]).toContain("no-store");

  await page.getByLabel("邀请码").fill("WRONG-CODE-2026");
  await page.getByRole("button", { name: "进入体验空间" }).click();
  await expect(page.locator(".demo-login-error")).toContainText("邀请码不正确");

  await page.getByLabel("邀请码").fill(inviteCode);
  await page.getByRole("button", { name: "进入体验空间" }).click();
  await expect(page).toHaveURL(/\/showcase$/);
  await expect(page.getByRole("heading", { level: 1, name: "沈家的回忆" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /包里还装着/ })).toBeVisible();
  await expect(page.getByLabel(/播放家庭影像/)).toBeVisible();

  const sessionCookie = (await context.cookies()).find((cookie) => cookie.name === "lingnian_demo_session");
  expect(sessionCookie?.httpOnly).toBe(true);
  expect(sessionCookie?.sameSite).toBe("Strict");

  const allowedMedia = await page.request.get("/showcase/shen-suqin-home.png");
  expect(allowedMedia.status()).toBe(200);
  expect(allowedMedia.headers()["x-robots-tag"]).toBe("noindex, nofollow");
  const allowedVideo = await page.request.get("/showcase/shen-suqin-story.mp4?v=4", {
    headers: { range: "bytes=0-1023" },
  });
  expect(allowedVideo.status()).toBe(206);
  expect(allowedVideo.headers()["content-type"]).toContain("video/mp4");
  expect((await allowedVideo.body()).byteLength).toBe(1024);
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(hasOverflow).toBe(false);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations, accessibility.violations.map((item) => `${item.id}: ${item.help}`).join("\n")).toEqual([]);

  await page.getByRole("button", { name: "退出体验" }).first().click();
  await expect(page).toHaveURL(/\/demo-login$/, { timeout: 15_000 });
  const blockedAgain = await page.request.get("/showcase/shen-suqin-home.png");
  expect(blockedAgain.status()).toBe(401);
});

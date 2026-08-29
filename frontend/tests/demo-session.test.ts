import { describe, expect, it } from "vitest";
import {
  createDemoSessionToken,
  DEMO_SESSION_MAX_AGE,
  isInviteCodeValid,
  verifyDemoSessionToken,
} from "@/lib/demo-auth/session";

const secret = "test-session-secret-with-at-least-32-characters";
const now = Date.UTC(2026, 7, 30, 12, 0, 0);

describe("邀请码演示会话", () => {
  it("只接受完整匹配的邀请码", async () => {
    const codes = ["LINGNIAN-2026-DEMO", "FAMILY-STORY-88"];
    await expect(isInviteCodeValid(" LINGNIAN-2026-DEMO ", codes)).resolves.toBe(true);
    await expect(isInviteCodeValid("lingnian-2026-demo", codes)).resolves.toBe(false);
    await expect(isInviteCodeValid("too-short", [])).resolves.toBe(false);
  });

  it("能创建并验证十二小时会话", async () => {
    const token = await createDemoSessionToken(secret, now);
    await expect(verifyDemoSessionToken(token, secret, now + 1_000)).resolves.toBe(true);
    await expect(
      verifyDemoSessionToken(token, secret, now + DEMO_SESSION_MAX_AGE * 1_000),
    ).resolves.toBe(false);
  });

  it("拒绝被篡改或使用错误密钥的会话", async () => {
    const token = await createDemoSessionToken(secret, now);
    const tampered = `${token.slice(0, -1)}${token.endsWith("a") ? "b" : "a"}`;
    await expect(verifyDemoSessionToken(tampered, secret, now)).resolves.toBe(false);
    await expect(verifyDemoSessionToken(token, `${secret}-wrong`, now)).resolves.toBe(false);
  });
});

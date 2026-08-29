import { NextRequest, NextResponse } from "next/server";
import {
  createDemoSessionToken,
  DEMO_SESSION_COOKIE,
  DEMO_SESSION_MAX_AGE,
  getConfiguredInviteCodes,
  getDemoSessionSecret,
  isDemoModeEnabled,
  isInviteCodeValid,
} from "@/lib/demo-auth/session";
import {
  canAttemptLogin,
  clearFailedLogins,
  isSameOriginRequest,
  isSecureRequest,
  recordFailedLogin,
} from "@/lib/demo-auth/request";

export const dynamic = "force-dynamic";

function json(message: string, status: number): NextResponse {
  return NextResponse.json(
    { ok: false, message },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isDemoModeEnabled()) return json("邀请码体验尚未开放。", 503);
  if (!isSameOriginRequest(request)) return json("请刷新页面后重试。", 403);
  if (!canAttemptLogin(request)) return json("尝试次数过多，请稍后再试。", 429);

  const secret = getDemoSessionSecret();
  const configuredCodes = getConfiguredInviteCodes();
  if (!secret || configuredCodes.length === 0) return json("体验空间正在准备中。", 503);

  let inviteCode = "";
  try {
    const body = await request.json() as { inviteCode?: unknown };
    if (typeof body.inviteCode === "string") inviteCode = body.inviteCode;
  } catch {
    return json("请输入邀请码。", 400);
  }

  if (!await isInviteCodeValid(inviteCode, configuredCodes)) {
    recordFailedLogin(request);
    return json("邀请码不正确，请检查后重试。", 401);
  }

  clearFailedLogins(request);
  const token = await createDemoSessionToken(secret);
  const response = NextResponse.json(
    { ok: true, redirectTo: "/showcase" },
    { headers: { "Cache-Control": "no-store" } },
  );
  response.cookies.set(DEMO_SESSION_COOKIE, token, {
    httpOnly: true,
    secure: isSecureRequest(request),
    sameSite: "strict",
    maxAge: DEMO_SESSION_MAX_AGE,
    path: "/",
    priority: "high",
  });
  return response;
}

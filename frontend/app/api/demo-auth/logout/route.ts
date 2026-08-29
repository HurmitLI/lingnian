import { NextRequest, NextResponse } from "next/server";
import { isSameOriginRequest, isSecureRequest } from "@/lib/demo-auth/request";
import { DEMO_SESSION_COOKIE } from "@/lib/demo-auth/session";

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isSameOriginRequest(request)) {
    return NextResponse.json({ ok: false }, { status: 403, headers: { "Cache-Control": "no-store" } });
  }

  const response = NextResponse.json(
    { ok: true, redirectTo: "/demo-login" },
    { headers: { "Cache-Control": "no-store" } },
  );
  response.cookies.set(DEMO_SESSION_COOKIE, "", {
    httpOnly: true,
    secure: isSecureRequest(request),
    sameSite: "strict",
    maxAge: 0,
    path: "/",
  });
  return response;
}

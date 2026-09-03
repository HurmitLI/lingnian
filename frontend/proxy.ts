import { NextRequest, NextResponse } from "next/server";
import {
  DEMO_SESSION_COOKIE,
  getDemoSessionSecret,
  isDemoModeEnabled,
  verifyDemoSessionToken,
} from "@/lib/demo-auth/session";

const PUBLIC_DEMO_PATHS = new Set([
  "/demo-login",
  "/api/demo-auth/login",
  "/api/demo-auth/logout",
]);

function secureDemoResponse(response: NextResponse): NextResponse {
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Cross-Origin-Resource-Policy", "same-origin");
  response.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}

function redirectTo(path: string, request: NextRequest): NextResponse {
  return secureDemoResponse(NextResponse.redirect(new URL(path, request.url)));
}

export async function proxy(request: NextRequest): Promise<NextResponse> {
  const formalAuthEnabled = process.env.FORMAL_AUTH_REQUIRED === "true";
  if (!isDemoModeEnabled() && !formalAuthEnabled) return NextResponse.next();

  if (formalAuthEnabled && !isDemoModeEnabled()) {
    const path = request.nextUrl.pathname;
    const cookieName = process.env.AUTH_COOKIE_NAME || "lingnian_session";
    const hasSession = Boolean(request.cookies.get(cookieName)?.value);
    const publicPaths = new Set(["/login", "/api/v1/auth/login", "/api/v1/auth/register"]);
    if (publicPaths.has(path)) return secureDemoResponse(NextResponse.next());
    if (hasSession) return secureDemoResponse(NextResponse.next());
    if (path.startsWith("/api/v1/")) {
      return secureDemoResponse(NextResponse.json(
        { error: { code: "AUTH_REQUIRED", message: "请先登录。" } },
        { status: 401 },
      ));
    }
    return redirectTo("/login", request);
  }

  const path = request.nextUrl.pathname;
  const secret = getDemoSessionSecret();
  const session = request.cookies.get(DEMO_SESSION_COOKIE)?.value;
  const isAuthenticated = Boolean(secret && await verifyDemoSessionToken(session, secret));

  if (PUBLIC_DEMO_PATHS.has(path)) {
    if (path === "/demo-login" && isAuthenticated) return redirectTo("/showcase", request);
    return secureDemoResponse(NextResponse.next());
  }

  if (path === "/showcase" || path === "/showcase/") {
    return isAuthenticated ? secureDemoResponse(NextResponse.next()) : redirectTo("/demo-login", request);
  }

  if (path.startsWith("/showcase/")) {
    if (isAuthenticated) return secureDemoResponse(NextResponse.next());
    return secureDemoResponse(new NextResponse("需要邀请码才能查看此内容。", {
      status: 401,
      headers: { "Cache-Control": "no-store", "Content-Type": "text/plain; charset=utf-8" },
    }));
  }

  return redirectTo(isAuthenticated ? "/showcase" : "/demo-login", request);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|brand/|favicon.ico).*)"],
};

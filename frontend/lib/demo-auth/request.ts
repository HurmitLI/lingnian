import type { NextRequest } from "next/server";

const ATTEMPT_WINDOW_MS = 10 * 60 * 1000;
const MAX_ATTEMPTS = 6;
const MAX_TRACKED_CLIENTS = 1_000;

type AttemptRecord = { count: number; resetAt: number };
const attempts = new Map<string, AttemptRecord>();

function getClientKey(request: NextRequest): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    || request.headers.get("x-real-ip")?.trim()
    || "unknown";
}

export function isSameOriginRequest(request: NextRequest): boolean {
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && !["same-origin", "same-site", "none"].includes(fetchSite)) return false;

  const origin = request.headers.get("origin");
  if (!origin) return true;

  const forwardedHost = request.headers.get("x-forwarded-host") || request.headers.get("host");
  if (!forwardedHost) return false;

  try {
    return new URL(origin).host === forwardedHost;
  } catch {
    return false;
  }
}

export function isSecureRequest(request: NextRequest): boolean {
  return request.nextUrl.protocol === "https:"
    || request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() === "https";
}

function pruneAttempts(now: number): void {
  if (attempts.size < MAX_TRACKED_CLIENTS) return;
  for (const [key, record] of attempts) {
    if (record.resetAt <= now) attempts.delete(key);
  }
  if (attempts.size >= MAX_TRACKED_CLIENTS) attempts.delete(attempts.keys().next().value ?? "");
}

export function canAttemptLogin(request: NextRequest, now = Date.now()): boolean {
  const record = attempts.get(getClientKey(request));
  if (!record || record.resetAt <= now) return true;
  return record.count < MAX_ATTEMPTS;
}

export function recordFailedLogin(request: NextRequest, now = Date.now()): void {
  pruneAttempts(now);
  const key = getClientKey(request);
  const record = attempts.get(key);
  if (!record || record.resetAt <= now) {
    attempts.set(key, { count: 1, resetAt: now + ATTEMPT_WINDOW_MS });
    return;
  }
  attempts.set(key, { ...record, count: record.count + 1 });
}

export function clearFailedLogins(request: NextRequest): void {
  attempts.delete(getClientKey(request));
}

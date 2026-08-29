const textEncoder = new TextEncoder();
const SESSION_DURATION_SECONDS = 12 * 60 * 60;
const MAX_CLOCK_SKEW_SECONDS = 60;

export const DEMO_SESSION_COOKIE = "lingnian_demo_session";
export const DEMO_SESSION_MAX_AGE = SESSION_DURATION_SECONDS;

type DemoSessionPayload = {
  v: 1;
  scope: "showcase";
  iat: number;
  exp: number;
};

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function stringToBase64Url(value: string): string {
  return bytesToBase64Url(textEncoder.encode(value));
}

function base64UrlToString(value: string): string | null {
  try {
    const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return null;
  }
}

async function hmac(value: string, secret: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, textEncoder.encode(value)));
}

async function sha256(value: string): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", textEncoder.encode(value)));
}

function constantTimeEqual(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
}

export function isDemoModeEnabled(): boolean {
  return process.env.DEMO_PUBLIC_MODE === "1";
}

export function getDemoSessionSecret(): string | null {
  const secret = process.env.DEMO_SESSION_SECRET?.trim();
  return secret && secret.length >= 32 ? secret : null;
}

export function getConfiguredInviteCodes(): string[] {
  return (process.env.DEMO_INVITE_CODES ?? "")
    .split(/[\n,]/u)
    .map((value) => value.trim())
    .filter(Boolean);
}

export async function isInviteCodeValid(candidate: string, configuredCodes: string[]): Promise<boolean> {
  const normalized = candidate.trim();
  if (normalized.length < 8 || normalized.length > 128 || configuredCodes.length === 0) return false;

  const candidateHash = await sha256(normalized);
  const results = await Promise.all(
    configuredCodes.map(async (configuredCode) => constantTimeEqual(candidateHash, await sha256(configuredCode))),
  );
  return results.some(Boolean);
}

export async function createDemoSessionToken(secret: string, now = Date.now()): Promise<string> {
  const issuedAt = Math.floor(now / 1000);
  const payload: DemoSessionPayload = {
    v: 1,
    scope: "showcase",
    iat: issuedAt,
    exp: issuedAt + SESSION_DURATION_SECONDS,
  };
  const encodedPayload = stringToBase64Url(JSON.stringify(payload));
  const signature = bytesToBase64Url(await hmac(encodedPayload, secret));
  return `${encodedPayload}.${signature}`;
}

export async function verifyDemoSessionToken(
  token: string | undefined,
  secret: string,
  now = Date.now(),
): Promise<boolean> {
  if (!token || token.length > 1024) return false;
  const [encodedPayload, encodedSignature, extra] = token.split(".");
  if (!encodedPayload || !encodedSignature || extra) return false;

  const expectedSignature = bytesToBase64Url(await hmac(encodedPayload, secret));
  const signaturesMatch = constantTimeEqual(
    textEncoder.encode(encodedSignature),
    textEncoder.encode(expectedSignature),
  );
  if (!signaturesMatch) return false;

  const decodedPayload = base64UrlToString(encodedPayload);
  if (!decodedPayload) return false;

  try {
    const payload = JSON.parse(decodedPayload) as Partial<DemoSessionPayload>;
    const currentTime = Math.floor(now / 1000);
    return (
      payload.v === 1
      && payload.scope === "showcase"
      && typeof payload.iat === "number"
      && typeof payload.exp === "number"
      && payload.iat <= currentTime + MAX_CLOCK_SKEW_SECONDS
      && payload.exp > currentTime
      && payload.exp - payload.iat === SESSION_DURATION_SECONDS
    );
  } catch {
    return false;
  }
}

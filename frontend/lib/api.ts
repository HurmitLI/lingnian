const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8011";

export class ApiError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      payload?.error?.code ?? "REQUEST_FAILED",
      payload?.error?.message ?? "请求没有成功，请稍后重试。",
    );
  }
  return response.json() as Promise<T>;
}

export function mediaUrl(path: string | null): string | null {
  return path ? `${API_BASE}${path}` : null;
}


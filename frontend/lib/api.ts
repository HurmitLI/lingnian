const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export class ApiError extends Error {
  code: string;
  retryable: boolean;

  constructor(code: string, message: string, retryable = false) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.retryable = retryable;
  }
}

type ApiOptions = RequestInit & { timeoutMs?: number };

async function request(path: string, options: ApiOptions = {}): Promise<Response> {
  const { timeoutMs, signal: externalSignal, ...requestOptions } = options;
  const controller = new AbortController();
  const effectiveTimeout = timeoutMs ?? (options.body instanceof FormData ? 10 * 60 * 1000 : 2 * 60 * 1000);
  const timer = window.setTimeout(() => controller.abort(), effectiveTimeout);
  const abortFromOutside = () => controller.abort(externalSignal?.reason);
  externalSignal?.addEventListener("abort", abortFromOutside, { once: true });
  if (externalSignal?.aborted) abortFromOutside();

  try {
    return await fetch(`${API_BASE}${path}`, {
      ...requestOptions,
      signal: controller.signal,
      headers: {
        ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...options.headers,
      },
    });
  } catch (value) {
    if (externalSignal?.aborted) throw value;
    if (controller.signal.aborted) {
      throw new ApiError("REQUEST_TIMEOUT", "等待时间比平时久，请稍后重试；已经保存的内容不会丢失。", true);
    }
    throw new ApiError("NETWORK_UNAVAILABLE", "暂时无法连接本机服务。请确认聆年仍在运行，然后重试。", true);
  } finally {
    window.clearTimeout(timer);
    externalSignal?.removeEventListener("abort", abortFromOutside);
  }
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const payload = await response.json().catch(() => null);
  if (
    response.status === 401
    && typeof window !== "undefined"
    && process.env.NEXT_PUBLIC_FORMAL_AUTH_REQUIRED === "true"
    && window.location.pathname !== "/login"
  ) {
    window.location.replace("/login");
  }
  return new ApiError(
    payload?.error?.code ?? "REQUEST_FAILED",
    payload?.error?.message ?? fallback,
    response.status === 408 || response.status === 429 || response.status >= 500,
  );
}

export async function api<T>(path: string, options?: ApiOptions): Promise<T> {
  const response = await request(path, options);
  if (!response.ok) {
    throw await responseError(response, "请求没有成功，请稍后重试。");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function apiDownload(
  path: string,
  options?: ApiOptions,
): Promise<{ blob: Blob; filename: string }> {
  const response = await request(path, {
    ...options,
    headers: {
      ...options?.headers,
    },
  });
  if (!response.ok) {
    throw await responseError(response, "下载没有成功，请稍后重试。");
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: match?.[1] ?? "niannian-recovery.json",
  };
}

export function mediaUrl(path: string | null): string | null {
  return path ? `${API_BASE}${path}` : null;
}

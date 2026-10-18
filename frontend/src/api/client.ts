/** Thin fetch wrapper: same-origin cookies, CSRF header, typed errors. */

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  requestId?: string;
  /** seconds from the Retry-After header of a 429 / 503 response */
  retryAfter?: number;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId?: string,
    retryAfter?: number,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}

/** Parses Retry-After (delta seconds or HTTP date); undefined when absent or invalid. */
export function parseRetryAfter(value: string | null): number | undefined {
  if (!value) return undefined;
  const s = Number(value);
  if (Number.isFinite(s)) return Math.max(0, Math.ceil(s));
  const at = Date.parse(value);
  return Number.isNaN(at) ? undefined : Math.max(0, Math.ceil((at - Date.now()) / 1000));
}

const NO_RETRY = [401, 403, 404, 422, 428];

/** React Query retry policy: no retry for client errors; a rate-limited request is retried
 *  once after the server's Retry-After (at most a minute); other failures twice. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError) {
    if (NO_RETRY.includes(error.status)) return false;
    if (error.status === 429) return failureCount < 1 && (error.retryAfter ?? 1) <= 60;
  }
  return failureCount < 2;
}

export function retryDelay(failureCount: number, error: unknown): number {
  if (error instanceof ApiError && error.status === 429) return Math.min(error.retryAfter ?? 1, 60) * 1000;
  return Math.min(1000 * 2 ** failureCount, 30_000);
}

function csrfToken(): string | undefined {
  const m = document.cookie.match(/(?:^|;\s*)dbm_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : undefined;
}

type Query = Record<string, string | number | boolean | string[] | null | undefined>;

export function buildUrl(path: string, query?: Query): string {
  const url = new URL(path.startsWith("/api") ? path : `/api/v1${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, x));
      else url.searchParams.set(k, String(v));
    }
  }
  return url.pathname + url.search;
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T>(
  method: string,
  path: string,
  opts: { query?: Query; body?: unknown; signal?: AbortSignal; raw?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (!["GET", "HEAD"].includes(method)) {
    const t = csrfToken();
    if (t) headers["X-CSRF-Token"] = t;
  }
  const res = await fetch(buildUrl(path, opts.query), {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  if (res.status === 401 && !path.startsWith("/auth/login")) onUnauthorized?.();
  if (!res.ok) {
    let payload: { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } } = {};
    try {
      payload = await res.json();
    } catch {
      /* non-JSON error */
    }
    const e = payload.error ?? {};
    throw new ApiError(
      res.status,
      e.code ?? "http_error",
      e.message ?? res.statusText,
      e.details ?? {},
      e.request_id,
      parseRetryAfter(res.headers.get("Retry-After")),
    );
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T,>(path: string, query?: Query, signal?: AbortSignal) => api<T>("GET", path, { query, signal });
export const post = <T,>(path: string, body?: unknown, query?: Query) => api<T>("POST", path, { body, query });
export const put = <T,>(path: string, body?: unknown) => api<T>("PUT", path, { body });
export const patch = <T,>(path: string, body?: unknown) => api<T>("PATCH", path, { body });
export const del = <T,>(path: string, query?: Query) => api<T>("DELETE", path, { query });

/** Download a file produced by a GET or POST endpoint. */
export async function download(path: string, filename: string, opts: { method?: string; body?: unknown; query?: Query } = {}) {
  const res = await api<Response>(opts.method ?? "GET", path, { body: opts.body, query: opts.query, raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

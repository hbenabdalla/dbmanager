/** NDJSON reader for staged endpoints (POST /servers/test-connection/stream). */
import { ApiError, buildUrl } from "@/api/client";

function csrf(): string | undefined {
  const m = document.cookie.match(/(?:^|;\s*)dbm_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : undefined;
}

/** POSTs `body` and calls `onEvent` for every JSON line as it arrives. Resolves when the stream ends. */
export async function postNdjson<E>(path: string, body: unknown, onEvent: (e: E) => void, signal?: AbortSignal): Promise<void> {
  const headers: Record<string, string> = { Accept: "application/x-ndjson", "Content-Type": "application/json" };
  const t = csrf();
  if (t) headers["X-CSRF-Token"] = t;
  const res = await fetch(buildUrl(path), { method: "POST", headers, credentials: "same-origin", body: JSON.stringify(body), signal });
  if (!res.ok) {
    let e: { code?: string; message?: string; details?: Record<string, unknown> } = {};
    try { e = ((await res.json()) as { error?: typeof e }).error ?? {}; } catch { /* non-JSON */ }
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.details ?? {});
  }
  const emit = (line: string) => {
    const s = line.trim();
    if (!s) return;
    try { onEvent(JSON.parse(s) as E); } catch { /* ignore a malformed line */ }
  };
  if (!res.body || typeof res.body.getReader !== "function") {
    (await res.text()).split("\n").forEach(emit);
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      emit(buf.slice(0, nl));
      buf = buf.slice(nl + 1);
    }
  }
  emit(buf + dec.decode());
}

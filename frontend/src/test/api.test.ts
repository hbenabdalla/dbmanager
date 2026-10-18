import { ApiError, api, buildUrl, parseRetryAfter, retryDelay, shouldRetry } from "@/api/client";
import { mockFetch } from "./utils";

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the CSRF cookie value on unsafe methods only", async () => {
    document.cookie = "dbm_csrf=tok123";
    const fetch = mockFetch(() => ({ body: { ok: true } }));
    await api("POST", "/servers", { body: { a: 1 } });
    await api("GET", "/servers");
    const [, postInit] = fetch.mock.calls[0];
    const [, getInit] = fetch.mock.calls[1];
    expect((postInit!.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok123");
    expect((getInit!.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
    expect(postInit!.credentials).toBe("same-origin");
  });

  it("maps error envelopes to ApiError", async () => {
    mockFetch(() => ({ status: 428, body: { error: { code: "confirmation_required", message: "Confirm", details: { expected: "server name" }, request_id: "r1" } } }));
    await expect(api("POST", "/workspace/execute", { body: {} })).rejects.toMatchObject({ status: 428, code: "confirmation_required", requestId: "r1" });
  });

  it("builds versioned URLs with repeated array params", () => {
    expect(buildUrl("/servers", { engine: ["oracle", "db2"], q: "", page: 2 })).toBe("/api/v1/servers?engine=oracle&engine=db2&page=2");
  });

  it("ApiError keeps details", () => {
    const e = new ApiError(403, "permission_denied", "nope", { permission: "query.modify" });
    expect(e.details.permission).toBe("query.modify");
  });

  it("reads Retry-After from a rate-limited response", async () => {
    mockFetch(() => ({
      status: 429,
      headers: { "Retry-After": "17" },
      body: { error: { code: "rate_limited", message: "Too many expensive operations. Try again in 17 s.", details: { policy: "expensive" } } },
    }));
    await expect(api("POST", "/workspace/execute", { body: {} })).rejects.toMatchObject({ status: 429, code: "rate_limited", retryAfter: 17 });
  });

  it("parses Retry-After seconds and dates", () => {
    expect(parseRetryAfter("5")).toBe(5);
    expect(parseRetryAfter(null)).toBeUndefined();
    expect(parseRetryAfter("soon")).toBeUndefined();
    expect(parseRetryAfter(new Date(Date.now() + 30_000).toUTCString())).toBeGreaterThanOrEqual(28);
  });

  it("retries a 429 once after Retry-After and never retries client errors", () => {
    const limited = new ApiError(429, "rate_limited", "slow down", {}, undefined, 12);
    expect(shouldRetry(0, limited)).toBe(true);
    expect(shouldRetry(1, limited)).toBe(false);
    expect(retryDelay(0, limited)).toBe(12_000);
    expect(shouldRetry(0, new ApiError(429, "rate_limited", "x", {}, undefined, 600))).toBe(false);
    expect(shouldRetry(0, new ApiError(403, "permission_denied", "no"))).toBe(false);
    expect(shouldRetry(1, new Error("network"))).toBe(true);
    expect(shouldRetry(2, new Error("network"))).toBe(false);
  });
});

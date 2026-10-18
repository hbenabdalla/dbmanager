import { ApiError, api, buildUrl } from "@/api/client";
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
});

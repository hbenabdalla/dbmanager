import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiTokensPage, type ApiTokenRow, UserTokens } from "@/features/account/api-tokens";
import { mockFetch, renderWithProviders } from "./utils";

const NOW = new Date().toISOString();
const LATER = new Date(Date.now() + 30 * 86400_000).toISOString();
const row = (over: Partial<ApiTokenRow> = {}): ApiTokenRow => ({
  id: "t1", name: "ci-reader", prefix: "abcdefghij", scopes: null, allowed_cidrs: null, created_at: NOW, expires_at: LATER,
  last_used_at: null, last_used_ip: null, revoked_at: null, created_by: "dba", status: "active", ...over,
});
const SECRET = "dbm_klmnopqrst_" + "S".repeat(43);

describe("API tokens", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("creates a scoped token, shows the secret once and revokes with confirmation", async () => {
    const user = userEvent.setup();
    let tokens: ApiTokenRow[] = [row()];
    let created: Record<string, unknown> | null = null;
    let revoked: string | null = null;
    mockFetch((url, init) => {
      const m = init.method ?? "GET";
      if (url.pathname === "/api/v1/me/tokens/policy") return { body: { max_days: 90, available_scopes: ["alerts.view", "server.view"] } };
      if (url.pathname === "/api/v1/me/tokens" && m === "GET") return { body: tokens };
      if (url.pathname === "/api/v1/me/tokens" && m === "POST") {
        created = JSON.parse(String(init.body));
        const t = row({ id: "t2", name: "inventory", prefix: "klmnopqrst", scopes: ["server.view"] });
        tokens = [t, ...tokens];
        return { status: 201, body: { ...t, token: SECRET } };
      }
      if (url.pathname.startsWith("/api/v1/me/tokens/") && m === "DELETE") {
        revoked = url.pathname.split("/").pop()!;
        tokens = tokens.map((t) => (t.id === revoked ? { ...t, status: "revoked" as const, revoked_at: NOW } : t));
        return { body: { message: "ok" } };
      }
      return undefined;
    });
    renderWithProviders(<ApiTokensPage />);
    expect(await screen.findByText("ci-reader")).toBeInTheDocument();
    expect(screen.getByText("dbm_abcdefghij_…")).toBeInTheDocument();
    expect(screen.getByText("Never")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Create token/ }));
    const dlg = await screen.findByRole("dialog");
    await user.type(within(dlg).getByPlaceholderText(/grafana/), "inventory");
    // expiry choices are capped by the server policy
    await waitFor(() => expect(within(dlg).queryByRole("option", { name: "180 days" })).toBeNull());
    expect(within(dlg).getByRole("option", { name: "90 days" })).toBeInTheDocument();
    await user.click(within(dlg).getByLabelText(/Only selected permissions/));
    const create = within(dlg).getByRole("button", { name: "Create token" });
    expect(create).toBeDisabled(); // a restricted token needs at least one permission
    await user.click(await within(dlg).findByLabelText("server.view"));
    await user.click(create);

    await waitFor(() => expect(created).toEqual({ name: "inventory", expires_in_days: 90, scopes: ["server.view"], allowed_cidrs: null }));
    expect(await screen.findByTestId("new-token")).toHaveTextContent(SECRET);
    await user.click(screen.getByRole("button", { name: "I have stored it" }));
    await waitFor(() => expect(screen.queryByTestId("new-token")).toBeNull());
    expect(screen.queryByText(SECRET)).toBeNull(); // never shown again
    expect(await screen.findByText("inventory")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Revoke ci-reader" }));
    const confirm = await screen.findByRole("dialog");
    await user.click(within(confirm).getByRole("button", { name: "Revoke token" }));
    await waitFor(() => expect(revoked).toBe("t1"));
    expect(await screen.findByText("revoked")).toBeInTheDocument();
  });

  it("lets an administrator see and revoke a user's tokens", async () => {
    const user = userEvent.setup();
    let revokedPath: string | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/users/u9/tokens") return { body: [row({ name: "etl", scopes: ["server.view"], allowed_cidrs: ["10.0.0.0/8"] })] };
      if (url.pathname === "/api/v1/users/u9/tokens/t1" && init.method === "DELETE") { revokedPath = url.pathname; return { body: { message: "ok" } }; }
      return undefined;
    });
    renderWithProviders(<UserTokens userId="u9" />);
    expect(await screen.findByText("etl")).toBeInTheDocument();
    expect(screen.getByText(/From 10.0.0.0\/8/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revoke etl" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Revoke token" }));
    await waitFor(() => expect(revokedPath).toBe("/api/v1/users/u9/tokens/t1"));
  });
});

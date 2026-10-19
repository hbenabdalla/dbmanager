import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { AppRoutes } from "@/App";
import type { ServerDetail } from "@/api/types";
import { TooltipProvider } from "@/components/ui/tooltip";
import { qrDataUrl } from "@/features/account/security";
import { SettingsTab } from "@/features/server/tabs/settings";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";

const ME = (perms: string[], platform: string[] = [], extra: Record<string, unknown> = {}) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [{ role: "Platform Administrator", scope_type: "platform", scope_id: null, scope_label: "Platform" }],
  mfa_enabled: false, mfa_enrollment_required: false, ...extra,
});
const USER = (id: string, username: string, auth_source = "local") => ({
  id, username, email: null, display_name: null, auth_source, is_active: true, must_change_password: false, locked_until: null,
  last_login_at: null, created_at: "2026-10-01T09:00:00Z", groups: [],
});
const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 50 });

type Reply = { status?: number; body: unknown } | Response | undefined;
type Call = { path: string; method: string; body: unknown };

/** fetch stub routed by "METHOD /path"; records every call. */
function stub(handler: (path: string, method: string, body: unknown) => Reply) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api\/v1/, "");
    calls.push({ path, method, body });
    const r = handler(path, method, body);
    if (r instanceof Response) return r;
    const out = r ?? { status: 404, body: { error: { code: "not_found", message: "not mocked" } } };
    if (out.status === 204) return new Response(null, { status: 204 });
    return new Response(JSON.stringify(out.body), { status: out.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}
const ndjson = (lines: unknown[]) =>
  new Response(lines.map((l) => JSON.stringify(l)).join("\n") + "\n", { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
const err = (status: number, code: string, message: string) => ({ status, body: { error: { code, message } } });

function providers(children: React.ReactNode, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider>{children}</TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider><Toaster /></ThemeProvider>,
  );
}
const renderApp = (route: string) => providers(<AppRoutes />, route);
const dialog = (name: string | RegExp) => screen.findByRole("dialog", { name });
const last = (calls: Call[], method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path);

function blobStubs() {
  const created = vi.fn((_b: Blob) => "blob:test");
  URL.createObjectURL = created as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
  const clicks: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicks.push(this.download); });
  return { created, clicks };
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("MFA sign-in", () => {
  it("asks for the code after the password and signs in", async () => {
    let signedIn = false;
    const calls = stub((path, method, body) => {
      if (path === "/auth/me") return signedIn ? { body: ME(["server.view"]) } : err(401, "not_authenticated", "no");
      if (path === "/auth/providers") return { body: [] };
      if (path === "/auth/login") return { body: { mfa_required: true, mfa_token: "tok-1", methods: ["totp", "recovery"] } };
      if (path === "/auth/login/mfa" && method === "POST") {
        if ((body as { code: string }).code !== "654321") return err(401, "not_authenticated", "Invalid authentication code");
        signedIn = true;
        return { body: ME(["server.view"]) };
      }
      if (path === "/dashboard/summary") return { body: { servers: 0 } };
      return undefined;
    });
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Username"), "dba");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Two-step verification" })).toBeInTheDocument();
    expect(signedIn).toBe(false);
    await userEvent.type(screen.getByLabelText("Authentication code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid authentication code");
    await userEvent.clear(screen.getByLabelText("Authentication code"));
    await userEvent.type(screen.getByLabelText("Authentication code"), "654321");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Two-step verification" })).not.toBeInTheDocument());
    expect(last(calls, "POST", "/auth/login/mfa")?.body).toEqual({ mfa_token: "tok-1", code: "654321" });
  });

  it("returns to the password step when the challenge expired", async () => {
    stub((path) => {
      if (path === "/auth/me") return err(401, "not_authenticated", "no");
      if (path === "/auth/providers") return { body: [] };
      if (path === "/auth/login") return { body: { mfa_required: true, mfa_token: "tok-1", methods: ["totp"] } };
      if (path === "/auth/login/mfa") return err(401, "not_authenticated", "Sign-in expired; enter your password again");
      return undefined;
    });
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Username"), "dba");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.type(await screen.findByLabelText("Authentication code"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in expired");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
});

describe("account security page", () => {
  it("renders the QR code as an image data URL", () => {
    expect(qrDataUrl('<svg a="1"></svg>')).toBe("data:image/svg+xml;charset=utf-8,%3Csvg%20a%3D%221%22%3E%3C%2Fsvg%3E");
  });

  it("enrolls an authenticator and shows the recovery codes once", async () => {
    let enabled = false;
    const { created, clicks } = blobStubs();
    const codes = Array.from({ length: 10 }, (_, i) => `AAA${i}11-BBB${i}22`);
    const calls = stub((path, method, body) => {
      if (path === "/auth/me") return { body: ME([], [], { mfa_enabled: enabled }) };
      if (path === "/me/mfa") return { body: { available: true, enabled, confirmed_at: enabled ? "2026-10-19T08:00:00Z" : null, recovery_codes_left: enabled ? 10 : 0, required: false, enrollment_pending: false } };
      if (path === "/me/mfa/totp/setup" && method === "POST") {
        if ((body as { password: string }).password !== "pw") return err(401, "not_authenticated", "Invalid password");
        return { body: { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/DB%20Manager:dba?secret=JBSWY3DPEHPK3PXP", qr_svg: "<svg></svg>" } };
      }
      if (path === "/me/mfa/totp/enable" && method === "POST") { enabled = true; return { body: { enabled: true, recovery_codes: codes } }; }
      return undefined;
    });
    renderApp("/account/security");
    await userEvent.click(await screen.findByRole("button", { name: "Set up authenticator" }));
    const d = await dialog("Set up an authenticator app");
    await userEvent.type(within(d).getByLabelText("Current password"), "wrong");
    await userEvent.click(within(d).getByRole("button", { name: "Continue" }));
    expect(await within(d).findByRole("alert")).toHaveTextContent("Invalid password");
    // a wrong password (401) keeps the session: the dialog stays open
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    await userEvent.clear(within(d).getByLabelText("Current password"));
    await userEvent.type(within(d).getByLabelText("Current password"), "pw");
    await userEvent.click(within(d).getByRole("button", { name: "Continue" }));
    const img = await within(d).findByRole("img", { name: "QR code for your authenticator app" });
    expect(img.getAttribute("src")).toBe(qrDataUrl("<svg></svg>"));
    expect(within(d).getByTestId("totp-secret")).toHaveTextContent("JBSWY3DPEHPK3PXP");
    expect(within(d).getByRole("button", { name: "Turn on" })).toBeDisabled();
    await userEvent.type(within(d).getByLabelText("6-digit code"), "123456");
    await userEvent.click(within(d).getByRole("button", { name: "Turn on" }));
    expect(last(calls, "POST", "/me/mfa/totp/enable")?.body).toEqual({ code: "123456" });
    const rc = await dialog("Store your recovery codes now");
    expect(within(rc).getByTestId("recovery-codes").querySelectorAll("li")).toHaveLength(10);
    await userEvent.click(within(rc).getByRole("button", { name: /Download \.txt/ }));
    expect(clicks).toEqual(["dbmanager-recovery-codes-dba.txt"]);
    const text = await new Promise<string>((ok) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.readAsText(created.mock.calls[0][0]); });
    expect(text).toContain(codes[9]);
    await userEvent.click(within(rc).getByRole("button", { name: "I have stored them" }));
    expect(await screen.findByTestId("codes-left")).toHaveTextContent("10");
  });

  it("turns MFA off and generates new recovery codes with password and code", async () => {
    let enabled = true;
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME([], [], { mfa_enabled: enabled }) };
      if (path === "/me/mfa") return { body: { available: true, enabled, confirmed_at: "2026-10-19T08:00:00Z", recovery_codes_left: 2, required: false, enrollment_pending: false } };
      if (path === "/me/mfa/recovery-codes" && method === "POST") return { body: { recovery_codes: ["NEW111-NEW222"] } };
      if (path === "/me/mfa/totp/disable" && method === "POST") { enabled = false; return { body: { message: "ok" } }; }
      return undefined;
    });
    renderApp("/account/security");
    expect(await screen.findByTestId("codes-left")).toHaveTextContent("2");
    await userEvent.click(screen.getByRole("button", { name: "New recovery codes" }));
    let d = await dialog("Generate new recovery codes");
    await userEvent.type(within(d).getByLabelText("Current password"), "pw");
    await userEvent.type(within(d).getByLabelText("Authenticator or recovery code"), "111222");
    await userEvent.click(within(d).getByRole("button", { name: "Generate" }));
    const rc = await dialog("Store your recovery codes now");
    expect(within(rc).getByTestId("recovery-codes")).toHaveTextContent("NEW111-NEW222");
    expect(last(calls, "POST", "/me/mfa/recovery-codes")?.body).toEqual({ password: "pw", code: "111222" });
    await userEvent.click(within(rc).getByRole("button", { name: "I have stored them" }));

    await userEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    d = await dialog("Turn off two-step verification?");
    await userEvent.type(within(d).getByLabelText("Current password"), "pw");
    await userEvent.type(within(d).getByLabelText("Authenticator or recovery code"), "333444");
    await userEvent.click(within(d).getByRole("button", { name: "Turn off" }));
    expect(await screen.findByRole("button", { name: "Set up authenticator" })).toBeInTheDocument();
    expect(last(calls, "POST", "/me/mfa/totp/disable")?.body).toEqual({ password: "pw", code: "333444" });
  });

  it("sends users who must enroll to the security page", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view"], ["users.manage"], { mfa_enrollment_required: true }) };
      if (path === "/me/mfa") return { body: { available: true, enabled: false, confirmed_at: null, recovery_codes_left: 0, required: true, enrollment_pending: true } };
      if (path.startsWith("/me/mfa")) return undefined;
      return err(403, "mfa_enrollment_required", "Set up multi-factor authentication first");
    });
    renderApp("/platform/users");
    expect(await screen.findByRole("heading", { name: "Security", level: 1 })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Set up authenticator" })).toBeInTheDocument();
    expect(screen.getAllByRole("alert").some((a) => /requires multi-factor authentication/.test(a.textContent ?? ""))).toBe(true);
    expect(screen.getByText("Required")).toBeInTheDocument();
  });

  it("explains that directory and SSO accounts use their identity provider", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME([], [], { auth_source: "oidc" }) };
      if (path === "/me/mfa") return { body: { available: false, enabled: false, confirmed_at: null, recovery_codes_left: 0, required: false, enrollment_pending: false } };
      return undefined;
    });
    renderApp("/account/security");
    expect(await screen.findByText(/provided by your identity provider/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set up authenticator" })).not.toBeInTheDocument();
  });
});

describe("user and group administration", () => {
  const users = [USER("u1", "dba"), { ...USER("u2", "bob"), mfa_enabled: true }, USER("u3", "carol"), USER("u4", "ldapuser", "ldap")];
  const group = { id: "g1", name: "DBA team", description: null, members: [{ id: "u2", username: "bob", display_name: null }] };
  const routes = (path: string, method: string): Reply => {
    if (path === "/auth/me") return { body: ME([], ["users.manage"]) };
    if (path === "/users" && method === "GET") return { body: page(users) };
    if (path === "/groups" && method === "GET") return { body: [group] };
    if (path === "/users/u2/mfa/reset" && method === "POST") return { body: { message: "MFA reset for bob" } };
    if (path === "/users/u2" && method === "DELETE") return { status: 204, body: null };
    if (path === "/users/u2/effective-permissions") return { body: { user: "bob", grants: [
      { role: "DBA Administrator", scope_type: "environment", scope_id: "e1", scope_label: "Production", via_group: "DBA team", permissions: ["server.view", "sessions.kill"] },
    ] } };
    if (path === "/groups/g1/members" && method === "PUT") return { body: group };
    if (path === "/groups/g1" && method === "DELETE") return { status: 204, body: null };
    return undefined;
  };

  it("resets MFA, shows effective permissions and deletes a user", async () => {
    const calls = stub(routes);
    renderApp("/platform/users");
    await userEvent.click((await screen.findAllByText("bob"))[0]);
    const d = await dialog("bob");
    await userEvent.click(within(d).getByRole("button", { name: "Show" }));
    const eff = await within(d).findByTestId("effective-permissions");
    expect(eff).toHaveTextContent("DBA Administrator");
    expect(eff).toHaveTextContent("via group DBA team");
    expect(eff).toHaveTextContent("sessions.kill");

    await userEvent.click(within(d).getByRole("button", { name: /Reset MFA/ }));
    const c = await dialog("Reset MFA of bob?");
    await userEvent.click(within(c).getByRole("button", { name: "Remove authenticator" }));
    expect(await screen.findByText("MFA reset for bob")).toBeInTheDocument();
    expect(last(calls, "POST", "/users/u2/mfa/reset")).toBeTruthy();

    await userEvent.click(within(await dialog("bob")).getByRole("button", { name: /Delete user/ }));
    const del = await dialog("Delete bob?");
    const confirm = within(del).getByRole("button", { name: "Delete user" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(del).getByLabelText("Confirmation text"), "bob");
    await userEvent.click(confirm);
    await waitFor(() => expect(last(calls, "DELETE", "/users/u2")).toBeTruthy());
  });

  it("offers no MFA reset for directory users and no self-delete", async () => {
    stub(routes);
    renderApp("/platform/users");
    await userEvent.click((await screen.findAllByText("ldapuser"))[0]);
    const d = await dialog("ldapuser");
    expect(within(d).queryByRole("button", { name: /Reset MFA/ })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click((await screen.findAllByText("dba"))[0]);
    const self = await dialog("dba");
    expect(within(self).queryByRole("button", { name: /Delete user/ })).not.toBeInTheDocument();
  });

  it("edits group members and deletes a group", async () => {
    const calls = stub(routes);
    renderApp("/platform/users");
    await userEvent.click(await screen.findByRole("tab", { name: "Groups" }));
    await userEvent.click(await screen.findByRole("button", { name: /DBA team/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Edit members/ }));
    const d = await dialog("Members of DBA team");
    expect(await within(d).findByLabelText("Member bob")).toBeChecked();
    expect(within(d).getByLabelText("Member dba")).toBeDisabled();
    await userEvent.click(within(d).getByLabelText("Member carol"));
    await userEvent.click(within(d).getByLabelText("Member bob"));
    await userEvent.click(within(d).getByRole("button", { name: "Save members" }));
    await waitFor(() => expect(last(calls, "PUT", "/groups/g1/members")?.body).toEqual({ user_ids: ["u3"] }));

    await userEvent.click(await screen.findByRole("button", { name: /Delete group/ }));
    const c = await dialog("Delete group DBA team?");
    await userEvent.type(within(c).getByLabelText("Confirmation text"), "DBA team");
    await userEvent.click(within(c).getByRole("button", { name: "Delete group" }));
    await waitFor(() => expect(last(calls, "DELETE", "/groups/g1")).toBeTruthy());
  });

  it("deletes a custom role but never a system role", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME([], ["roles.manage"]) };
      if (path === "/roles" && method === "GET") return { body: [
        { id: "r1", name: "Platform Administrator", description: null, is_system: true, permissions: [] },
        { id: "r2", name: "Night shift", description: null, is_system: false, permissions: ["server.view"] },
      ] };
      if (path === "/permissions") return { body: [{ key: "server.view", group: "Servers", description: "See servers", platform_only: false }] };
      if (path === "/roles/r2" && method === "DELETE") return { status: 204, body: null };
      return undefined;
    });
    renderApp("/platform/roles");
    await userEvent.click(await screen.findByRole("button", { name: /Night shift/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Delete role/ }));
    const c = await dialog("Delete role Night shift?");
    await userEvent.type(within(c).getByLabelText("Confirmation text"), "Night shift");
    await userEvent.click(within(c).getByRole("button", { name: "Delete role" }));
    await waitFor(() => expect(last(calls, "DELETE", "/roles/r2")).toBeTruthy());
  });
});

describe("placement management", () => {
  it("creates, edits and deletes placement items and explains a refused delete", async () => {
    let envs = [{ id: "e1", name: "Production", tier: "production", description: null, sort_order: 1, server_count: 3 }];
    const calls = stub((path, method, body) => {
      if (path === "/auth/me") return { body: ME(["server.view"], ["inventory.manage"]) };
      if (path === "/environments" && method === "GET") return { body: envs };
      if (path === "/environments" && method === "POST") {
        envs = [...envs, { id: "e2", ...(body as { name: string; tier: string; description: null; sort_order: number }), server_count: 0 }];
        return { status: 201, body: envs[1] };
      }
      if (path === "/environments/e1" && method === "PUT") return { body: envs[0] };
      if (path === "/environments/e1" && method === "DELETE") return err(409, "conflict", "3 servers still use this environment");
      if (path === "/environments/e2" && method === "DELETE") { envs = envs.slice(0, 1); return { status: 204, body: null }; }
      if (["/sites", "/clusters", "/applications"].includes(path)) return { body: [] };
      return undefined;
    });
    renderApp("/fleet/placement");
    expect(await screen.findByText("Production")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /New environment/ }));
    let d = await dialog("New environment");
    await userEvent.type(within(d).getByLabelText("Name"), "Staging");
    await userEvent.selectOptions(within(d).getByLabelText("Tier"), "uat");
    await userEvent.click(within(d).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(last(calls, "POST", "/environments")?.body).toEqual({ name: "Staging", tier: "uat", description: null, sort_order: 100 }));
    expect(await screen.findByText("Staging")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Edit Production" }));
    d = await dialog("Edit environment Production");
    await userEvent.type(within(d).getByLabelText("Description"), "Customer facing");
    await userEvent.click(within(d).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(last(calls, "PUT", "/environments/e1")?.body).toEqual({ name: "Production", tier: "production", description: "Customer facing", sort_order: 1 }));

    await userEvent.click(screen.getByRole("button", { name: "Delete Production" }));
    d = await dialog("Delete environment Production?");
    expect(d).toHaveTextContent("currently 3");
    await userEvent.click(within(d).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("3 servers still use this environment")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    await userEvent.click(await screen.findByRole("button", { name: "Delete Staging" }));
    d = await dialog("Delete environment Staging?");
    await userEvent.click(within(d).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByText("Staging")).not.toBeInTheDocument());
  });

  it("hides the placement editor without inventory.manage", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view"]) };
      if (path === "/environments") return { body: [{ id: "e1", name: "Production", tier: "production", description: null, sort_order: 1 }] };
      if (["/sites", "/clusters", "/applications"].includes(path)) return { body: [] };
      return undefined;
    });
    renderApp("/fleet/placement");
    expect(await screen.findByText("Production")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /New environment/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Production" })).not.toBeInTheDocument();
  });
});

describe("OIDC multi-factor settings", () => {
  it("stores require_mfa, acr and amr values in the provider configuration", async () => {
    const idp = { id: "p1", kind: "oidc", name: "Entra", enabled: true, has_secret: true, mappings: [],
      config: { issuer: "https://login.example/v2.0", client_id: "dbm", scopes: "openid profile email", redirect_uri_effective: "https://dbm/cb" } };
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME([], ["identity.manage", "users.manage"]) };
      if (path === "/identity-providers" && method === "GET") return { body: [idp] };
      if (path === "/identity-providers/p1" && method === "PATCH") return { body: idp };
      if (path === "/groups") return { body: [] };
      return undefined;
    });
    renderApp("/platform/identity");
    await userEvent.click(await screen.findByRole("button", { name: "All fields" }));
    const d = await dialog("Edit Entra");
    const acr = within(d).getByLabelText("Accepted acr values");
    expect(acr).toBeDisabled();
    await userEvent.click(within(d).getByLabelText(/Require multi-factor authentication/));
    await userEvent.type(acr, "loa2, loa3");
    expect(acr).toHaveValue("loa2, loa3");
    await userEvent.type(within(d).getByLabelText("Accepted amr values"), "mfa,otp");
    await userEvent.type(within(d).getByLabelText("Request acr_values"), "urn:mfa");
    await userEvent.click(within(d).getByRole("button", { name: "Save provider" }));
    await waitFor(() => expect(last(calls, "PATCH", "/identity-providers/p1")).toBeTruthy());
    const cfg = (last(calls, "PATCH", "/identity-providers/p1")!.body as { config: Record<string, unknown> }).config;
    expect(cfg).toMatchObject({ require_mfa: true, required_acr: ["loa2", "loa3"], mfa_amr_values: ["mfa", "otp"], acr_values: "urn:mfa" });
    expect(cfg).not.toHaveProperty("redirect_uri_effective");
  });
});

describe("server operations", () => {
  const SERVER = (permissions: string[]) => ({
    id: "s1", name: "PG-PROD-01", engine: "postgresql", host: "10.0.0.5", port: 5432, default_database: null, description: null,
    environment: null, site: null, cluster: null, cluster_role: null, application: null, owner: null, tls_mode: "verify-full",
    monitoring_enabled: true, collection_interval_s: 60, management_mode: "monitoring", connection_options: {},
    credentials: { monitoring: null, admin: null, readonly: null }, capabilities: {}, server_info: null, unavailable_metrics: {}, permissions,
  }) as unknown as ServerDetail;
  const base = (path: string): Reply => {
    if (path === "/auth/me") return { body: ME(["server.view"]) };
    if (["/environments", "/sites", "/clusters", "/applications", "/engines"].includes(path)) return { body: [] };
    return undefined;
  };

  it("downloads diagnostics, scans security and runs a staged connection test", async () => {
    const { clicks } = blobStubs();
    const calls = stub((path, method) => {
      if (path === "/servers/s1/diagnostics") return { body: { format: "dbmanager-diagnostics/1" } };
      if (path === "/servers/s1/security-scan" && method === "POST") return { body: { message: "Security scan queued" } };
      if (path === "/servers/s1/test-connection/stream" && method === "POST") return ndjson([
        { stage: "dns", status: "ok", message: "10.0.0.5", duration_ms: 1 },
        { stage: "tcp", status: "ok", message: "port open", duration_ms: 2 },
        { stage: "authenticate", status: "failed", message: "password authentication failed", duration_ms: 9 },
        { stage: "result", status: "failed", result: { ok: false, message: "password authentication failed" } },
      ]);
      return base(path);
    });
    providers(<SettingsTab server={SERVER(["server.view", "server.modify", "security.manage", "collectors.view"])} />);
    await userEvent.click(await screen.findByRole("button", { name: /Download diagnostics/ }));
    await waitFor(() => expect(clicks).toEqual(["dbm-diagnostics-PG-PROD-01.json"]));
    expect(last(calls, "GET", "/servers/s1/diagnostics")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /Scan security now/ }));
    expect(await screen.findByText("Security scan queued")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Test connection/ }));
    const d = await dialog("Test connection to PG-PROD-01");
    await userEvent.click(within(d).getByRole("button", { name: "Start test" }));
    expect(await within(d).findByRole("status")).toHaveTextContent("Connection failed. password authentication failed");
    expect(within(d).getByRole("button", { name: "Run again" })).toBeEnabled();
    expect(last(calls, "POST", "/servers/s1/test-connection/stream")?.body).toEqual({});
  });

  it("shows only the operations the role allows", async () => {
    stub(base);
    providers(<SettingsTab server={SERVER(["server.view"])} />);
    expect(await screen.findByText("Operations")).toBeInTheDocument();
    for (const name of [/Download diagnostics/, /Scan security now/, /Test connection/]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });
});

describe("reports", () => {
  it("offers only the formats the backend serves", async () => {
    const { clicks } = blobStubs();
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view", "reports.view"]) };
      if (path === "/reports") return { body: [
        { key: "inventory", title: "Server inventory", description: "All servers", formats: ["csv", "xlsx"], planned_formats: ["pdf"], available: true },
        { key: "findings", title: "Security findings", description: "Open findings", formats: ["csv"], planned_formats: ["xlsx", "pdf"], available: true },
      ] };
      if (path === "/reports/inventory.xlsx") return { body: {} };
      return undefined;
    });
    renderApp("/reports");
    expect(await screen.findByRole("button", { name: "Download Server inventory as XLSX" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Security findings as CSV" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download Security findings as XLSX" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /as PDF/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Download Server inventory as XLSX" }));
    await waitFor(() => expect(clicks).toEqual(["inventory.xlsx"]));
  });
});

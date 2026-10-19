import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { AppRoutes } from "@/App";
import type { ServerDetail } from "@/api/types";
import { TooltipProvider } from "@/components/ui/tooltip";
import { monitoringCredential } from "@/features/onboarding/onboarding-wizard";
import { SettingsTab } from "@/features/server/tabs/settings";
import { secretRefError, vaultStatus } from "@/features/shared/secret-source";
import { AuthProvider } from "@/lib/auth";
import { allowed, NAV_GROUPS } from "@/lib/nav";
import { ThemeProvider } from "@/lib/theme";

const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [], mfa_enabled: true, mfa_enrollment_required: false,
});
type Reply = { status?: number; body: unknown } | undefined;
type Call = { path: string; method: string; body: unknown };

function stub(handler: (path: string, method: string, body: unknown) => Reply) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api\/v1/, "");
    calls.push({ path, method, body });
    const out = handler(path, method, body) ?? { status: 404, body: { error: { code: "not_found", message: "not mocked" } } };
    if (out.status === 204) return new Response(null, { status: 204 });
    return new Response(JSON.stringify(out.body), { status: out.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}
function providers(children: React.ReactNode, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider>{children}</TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider><Toaster /></ThemeProvider>,
  );
}
const dialog = (name: string | RegExp) => screen.findByRole("dialog", { name });
const last = (calls: Call[], method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path);

const DEST = {
  id: "d1", name: "Corporate SIEM", kind: "syslog", enabled: true,
  config: { host: "siem.example.com", port: 6514, transport: "tls", facility: "audit" }, categories: ["auth"],
  has_signing_secret: false, cursor_id: 42, exported_count: 1200, pending: 3, last_success_at: "2026-10-18T09:00:00Z",
  last_error: null, last_error_at: null, created_by: "dba", created_at: "2026-10-01T09:00:00Z",
};
const HOOK = {
  ...DEST, id: "d2", name: "Splunk HEC", kind: "webhook", config: { url: "https://hec.example.com/dbm", headers: {} }, categories: [],
  has_signing_secret: true, pending: 0, last_error: "receiver answered HTTP 503", last_error_at: "2026-10-18T09:05:00Z",
};

describe("audit forwarding (SIEM destinations)", () => {
  it("lists destinations with backlog and errors, sends a test event and removes one", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME([], ["audit.forward"]) };
      if (path === "/platform/siem-destinations" && method === "GET") return { body: [DEST, HOOK] };
      if (path === "/platform/siem-destinations/d1/test") return { body: { ok: false, error: "ConnectionRefusedError: refused" } };
      if (path === "/platform/siem-destinations/d2" && method === "DELETE") return { body: { message: "removed" } };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/audit-forwarding");
    expect(await screen.findByText("Corporate SIEM")).toBeInTheDocument();
    expect(screen.getByText("tls://siem.example.com:6514")).toBeInTheDocument();
    expect(screen.getByText("3 pending")).toBeInTheDocument();
    expect(screen.getByText(/receiver answered HTTP 503/)).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: /Send test event/ })[0]);
    expect(await screen.findByText(/Test event to Corporate SIEM failed: ConnectionRefusedError/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Delete Splunk HEC" }));
    const c = await dialog("Remove Splunk HEC?");
    await userEvent.click(within(c).getByRole("button", { name: "Remove destination" }));
    await waitFor(() => expect(last(calls, "DELETE", "/platform/siem-destinations/d2")).toBeTruthy());
  });

  it("creates a TLS syslog destination with categories and start position", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME([], ["audit.forward"]) };
      if (path === "/platform/siem-destinations" && method === "GET") return { body: [] };
      if (path === "/platform/siem-destinations" && method === "POST") return { status: 201, body: DEST };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/audit-forwarding");
    await userEvent.click(await screen.findByRole("button", { name: /New destination/ }));
    const d = await dialog("New audit forwarding destination");
    const add = within(d).getByRole("button", { name: "Add destination" });
    expect(add).toBeDisabled();
    await userEvent.type(within(d).getByLabelText("Name"), "Corporate SIEM");
    await userEvent.type(within(d).getByLabelText("Host"), "siem.example.com");
    await userEvent.selectOptions(within(d).getByLabelText("Transport"), "tls");
    await userEvent.type(within(d).getByLabelText("CA certificate (PEM)"), "not a cert");
    expect(add).toBeDisabled();
    await userEvent.clear(within(d).getByLabelText("CA certificate (PEM)"));
    await userEvent.click(within(d).getByLabelText("auth"));
    await userEvent.click(within(d).getByLabelText("security"));
    await userEvent.selectOptions(within(d).getByLabelText("Start forwarding from"), "beginning");
    await userEvent.click(add);
    await waitFor(() => expect(last(calls, "POST", "/platform/siem-destinations")).toBeTruthy());
    expect(last(calls, "POST", "/platform/siem-destinations")?.body).toEqual({
      name: "Corporate SIEM", kind: "syslog", enabled: true, categories: ["auth", "security"], start_from: "beginning",
      config: { host: "siem.example.com", transport: "tls", facility: "audit" },
    });
  });

  it("keeps, replaces or clears a webhook signing secret on edit and refuses an Authorization header", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME([], ["audit.forward"]) };
      if (path === "/platform/siem-destinations" && method === "GET") return { body: [HOOK] };
      if (path === "/platform/siem-destinations/d2" && method === "PUT") return { body: HOOK };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/audit-forwarding");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Splunk HEC" }));
    let d = await dialog("Edit Splunk HEC");
    expect(within(d).getByLabelText("Type")).toBeDisabled();
    expect(within(d).queryByLabelText("Start forwarding from")).not.toBeInTheDocument();
    const headers = within(d).getByLabelText(/Extra headers/);
    await userEvent.type(headers, "Authorization: Bearer abc");
    expect(within(d).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(headers);
    await userEvent.click(within(d).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(last(calls, "PUT", "/platform/siem-destinations/d2")).toBeTruthy());
    const kept = last(calls, "PUT", "/platform/siem-destinations/d2")?.body as Record<string, unknown>;
    expect(kept).not.toHaveProperty("signing_secret");
    expect(kept).not.toHaveProperty("start_from");

    await userEvent.click(await screen.findByRole("button", { name: "Edit Splunk HEC" }));
    d = await dialog("Edit Splunk HEC");
    await userEvent.click(within(d).getByLabelText("Remove the stored signing secret"));
    await userEvent.click(within(d).getByRole("button", { name: "Save" }));
    await waitFor(() => expect((last(calls, "PUT", "/platform/siem-destinations/d2")?.body as Record<string, unknown>).signing_secret).toBe(""));
  });

  it("is in the Platform navigation only with audit.forward", () => {
    const items = NAV_GROUPS.flatMap((g) => g.sections).flatMap((sec) => (sec.items ?? []).map((it) => ({ sec, it })));
    const entry = items.find(({ it }) => it.to === "/platform/audit-forwarding");
    expect(entry?.it).toMatchObject({ label: "Audit forwarding", perm: "audit.forward", platform: true });
    expect(allowed(entry?.sec.perm, (p) => p === "audit.forward")).toBe(true);
    expect(allowed(entry?.it.perm, (p) => p === "audit.view")).toBe(false);
  });
});

describe("Vault credential provider", () => {
  it("validates references like the backend", () => {
    expect(secretRefError("vault", "databases/pg-prod#password")).toBeNull();
    expect(secretRefError("vault", "kv-dba:oracle/erp#password")).toBeNull();
    for (const bad of ["no-key", "../escape#x", "/abs#x", "a#b c", "a/../b#k"]) expect(secretRefError("vault", bad)).toMatch(/Vault references/);
    expect(secretRefError("env", "DBM_MASTER_KEY")).toMatch(/belong to the platform/);
    expect(secretRefError("env", "PG_ORDERS_PW")).toMatch(/belong to the platform/);
    expect(secretRefError("env", "ORDERS_PW")).toBeNull();
    expect(secretRefError("file", "secrets/x")).toMatch(/absolute/);
    expect(secretRefError("local", "")).toBeNull();
  });

  it("registers with only a reference, never a password, for external sources", () => {
    expect(monitoringCredential({ mon_user: "mon", mon_password: "pw", mon_provider: "local", mon_ref: "" })).toEqual({ username: "mon", password: "pw" });
    expect(monitoringCredential({ mon_user: "mon", mon_password: "typed-then-switched", mon_provider: "vault", mon_ref: " databases/pg#password " }))
      .toEqual({ username: "mon", password: null, provider: "vault", secret_ref: "databases/pg#password" });
  });

  it("summarizes the provider state", () => {
    expect(vaultStatus({ available: false, configured: false }).text).toMatch(/Not configured/);
    expect(vaultStatus({ available: true, configured: true, reachable: false, error: "ConnectError" })).toEqual({ tone: "crit", text: "Configured but unreachable (ConnectError)" });
    expect(vaultStatus({ available: true, configured: true, reachable: true, sealed: true }).tone).toBe("crit");
    expect(vaultStatus({ available: true, configured: true, reachable: true, sealed: false, initialized: true, version: "1.17.6", auth: "approle", kv_mount: "secret" }).text)
      .toBe('Reachable, Vault 1.17.6, approle auth, default mount "secret"');
  });

  const SERVER = {
    id: "s1", name: "PG-PROD-01", engine: "postgresql", host: "10.0.0.5", port: 5432, default_database: null, description: null,
    environment: null, site: null, cluster: null, cluster_role: null, application: null, owner: null, tls_mode: "verify-full",
    monitoring_enabled: true, collection_interval_s: 60, management_mode: "monitoring", connection_options: {},
    credentials: { monitoring: { purpose: "monitoring", username: "dbm_monitor", provider: "local", secret_ref: null, has_secret: true, rotated_at: null }, admin: null, readonly: null },
    capabilities: {}, server_info: null, unavailable_metrics: {}, permissions: ["server.view", "server.modify", "credentials.manage"],
  } as unknown as ServerDetail;
  const PROVIDERS = (vault: Record<string, unknown>) => ({
    local: { available: true }, env: { available: true }, file: { available: true, allowed_dirs: ["/run/secrets"] }, vault,
  });

  it("stores a Vault reference for a server credential and shows the provider status", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME(["server.view", "credentials.manage"]) };
      if (["/environments", "/sites", "/clusters", "/applications", "/engines"].includes(path)) return { body: [] };
      if (path === "/platform/secret-providers") return { body: PROVIDERS({ available: true, configured: true, reachable: true, sealed: false, initialized: true, version: "1.17.6", auth: "token", kv_mount: "secret" }) };
      if (path === "/servers/s1/credentials/monitoring" && method === "PUT") return { body: { message: "ok" } };
      return undefined;
    });
    providers(<SettingsTab server={SERVER} />);
    const source = (await screen.findAllByLabelText("Secret source"))[0];
    await waitFor(() => expect(within(source).getByRole("option", { name: "HashiCorp Vault (KV v2)" })).toBeInTheDocument());
    await userEvent.selectOptions(source, "vault");
    expect((await screen.findAllByTestId("vault-status"))[0]).toHaveTextContent("Vault: Reachable, Vault 1.17.6, token auth");
    const ref = screen.getByLabelText("Vault reference");
    const save = screen.getAllByRole("button", { name: "Save credential" })[0];
    await userEvent.type(ref, "../x#password");
    expect(save).toBeDisabled();
    expect(screen.getByText(/Vault references look like/)).toBeInTheDocument();
    await userEvent.clear(ref);
    await userEvent.type(ref, "kv-dba:databases/pg-prod#password");
    await userEvent.click(save);
    await waitFor(() => expect(last(calls, "PUT", "/servers/s1/credentials/monitoring")).toBeTruthy());
    expect(last(calls, "PUT", "/servers/s1/credentials/monitoring")?.body).toEqual({
      username: "dbm_monitor", password: null, provider: "vault", secret_ref: "kv-dba:databases/pg-prod#password",
    });
  });

  it("does not offer Vault when the installation has not configured it", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view", "credentials.manage"]) };
      if (["/environments", "/sites", "/clusters", "/applications", "/engines"].includes(path)) return { body: [] };
      if (path === "/platform/secret-providers") return { body: PROVIDERS({ available: false, configured: false, kv_mount: "secret" }) };
      return undefined;
    });
    providers(<SettingsTab server={SERVER} />);
    const source = (await screen.findAllByLabelText("Secret source"))[0];
    await waitFor(() => expect(within(source).getByRole("option", { name: "Mounted secret file" })).toBeInTheDocument());
    expect(within(source).queryByRole("option", { name: /Vault/ })).not.toBeInTheDocument();
  });

  it("shows the secret sources on the Credentials page", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["server.view", "credentials.manage"]) };
      if (path === "/credentials") return { body: [] };
      if (path === "/platform/secret-providers") return { body: PROVIDERS({ available: true, configured: true, reachable: false, error: "ConnectTimeout", auth: "approle", kv_mount: "secret" }) };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/credentials");
    const list = await screen.findByRole("list", { name: "Secret sources" });
    expect(within(list).getByText("HashiCorp Vault (KV v2)")).toBeInTheDocument();
    expect(within(list).getByText(/Configured but unreachable \(ConnectTimeout\)/)).toBeInTheDocument();
    expect(within(list).getByText(/allowed directories: \/run\/secrets/)).toBeInTheDocument();
  });
});

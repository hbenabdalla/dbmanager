import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { BulkOnboardingPage, csvToRecords, parseCsv, SECRET_MESSAGE, toRows } from "@/features/onboarding/bulk";
import { OnboardingWizard } from "@/features/onboarding/onboarding-wizard";
import { postNdjson } from "@/features/onboarding/stream";
import { LdapWizard, OidcWizard } from "@/features/platform/idp-wizards";
import { SetupChecklist } from "@/features/setup/checklist";
import { SetupWizardPage } from "@/features/setup/setup-wizard";
import { AuthProvider } from "@/lib/auth";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const ME = {
  id: "u1", username: "admin", display_name: null, email: null, auth_source: "local", must_change_password: false,
  permissions: ["server.register", "settings.manage", "idp.manage", "collectors.view", "inventory.manage"], platform_permissions: [], grants: [],
};
const ENGINES = [
  { engine: "postgresql", display_name: "PostgreSQL", default_port: 5432, driver: "asyncpg", implementation_phase: 2, implemented: true,
    support_level: "supported", support_note: "", driver_status: "installed", connection_options: [], licensable_features: [], capabilities: {} },
  { engine: "mssql", display_name: "Microsoft SQL Server", default_port: 1433, driver: "pyodbc", implementation_phase: 4, implemented: true,
    support_level: "supported", support_note: "", driver_status: "missing",
    driver_detail: { error: "no ODBC driver", remediation: "Microsoft ODBC Driver 18 is required. Rebuild the backend image." },
    connection_options: [], licensable_features: [], capabilities: {} },
];
const LISTS = ["/api/v1/environments", "/api/v1/sites", "/api/v1/clusters", "/api/v1/applications"];

function ndjson(lines: unknown[]) {
  return new Response(lines.map((l) => JSON.stringify(l)).join("\n") + "\n", { status: 200, headers: { "Content-Type": "application/x-ndjson" } });
}

describe("NDJSON reader", () => {
  it("emits one event per line, including a final line without newline", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"a":1}\n{"a":2}\n\n{"a":3}', { status: 200 })));
    const seen: number[] = [];
    await postNdjson<{ a: number }>("/x", {}, (e) => seen.push(e.a));
    expect(seen).toEqual([1, 2, 3]);
  });
});

describe("database onboarding wizard", () => {
  it("defaults to Monitoring Only, shows the missing-driver remediation and streams staged test results", async () => {
    let streamed: Record<string, unknown> | null = null;
    let registered: Record<string, unknown> | null = null;
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/engines") return json(ENGINES);
      if (LISTS.includes(url.pathname)) return json(url.pathname.endsWith("environments") ? [{ id: "e1", name: "Production", tier: "production", description: null, sort_order: 1 }] : []);
      if (url.pathname === "/api/v1/servers/test-connection/stream") {
        streamed = JSON.parse(String(init.body));
        return ndjson([
          { stage: "dns", status: "running", message: null }, { stage: "dns", status: "ok", message: "10.0.0.5", duration_ms: 2 },
          { stage: "tcp", status: "ok", message: "port open", duration_ms: 3 },
          { stage: "authenticate", status: "ok", message: "signed in as mon", duration_ms: 20 },
          { stage: "privileges", status: "warn", message: "pg_stat_statements is not installed", duration_ms: 5 },
          { stage: "result", status: "ok", result: { ok: true, message: "Connected", version: "16.4", latency_ms: 31,
            capabilities: { sessions: { level: "FULL" }, query_statistics: { level: "REQUIRES_EXTENSION", note: "install pg_stat_statements" } } } },
        ]);
      }
      if (url.pathname === "/api/v1/servers" && init.method === "POST") { registered = JSON.parse(String(init.body)); return json({ id: "s1", name: "pg-01", implementation: "implemented" }, 201); }
      return json({ error: { code: "not_found", message: "not mocked" } }, 404);
    }));
    renderWithProviders(<OnboardingWizard open onOpenChange={() => {}} />);
    await userEvent.click(await screen.findByRole("radio", { name: /Microsoft SQL Server/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("Microsoft ODBC Driver 18 is required.");
    await userEvent.click(screen.getByRole("radio", { name: /PostgreSQL/ }));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    // connection step validates before moving on
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(await screen.findByText("Enter a host name or IP address")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name"), "pg-01");
    await userEvent.type(screen.getByLabelText("Host"), "10.0.0.5");
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByRole("heading", { name: "TLS" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await userEvent.type(screen.getByLabelText("Monitoring user"), "mon");
    await userEvent.type(screen.getByLabelText("Monitoring password"), "S3cret-value-1");
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByRole("radio", { name: /Monitoring Only/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Administration Enabled/ })).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await userEvent.click(screen.getByRole("button", { name: /Run test/ }));
    const stages = await screen.findByRole("list", { name: "Test stages" });
    await waitFor(() => expect(within(stages).getByText("Authenticate").closest("li")).toHaveAttribute("data-status", "ok"));
    expect(within(stages).getByText("Check privileges").closest("li")).toHaveAttribute("data-status", "warn");
    expect(within(stages).getByText("TLS handshake").closest("li")).toHaveAttribute("data-status", "skipped");
    expect(await screen.findByText(/Connected, version 16.4/)).toBeInTheDocument();
    expect(screen.getByLabelText("Capabilities")).toHaveTextContent("install pg_stat_statements");
    expect(streamed).toMatchObject({ engine: "postgresql", host: "10.0.0.5", port: 5432, username: "mon", tls_mode: "verify-full" });
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByText("Monitoring Only")).toBeInTheDocument();
    expect(screen.queryByText("S3cret-value-1")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Register server/ }));
    expect(await screen.findByText("pg-01 registered")).toBeInTheDocument();
    expect(registered).toMatchObject({ management_mode: "monitoring", admin_credential: null, monitoring_credential: { username: "mon" } });
  });
});

describe("bulk onboarding", () => {
  it("parses quoted CSV and strips password columns", () => {
    expect(parseCsv('a,b\n"x, y","say ""hi"""\r\n')).toEqual([["a", "b"], ["x, y", 'say "hi"']]);
    const recs = csvToRecords("name,engine,host,password\npg-01,postgresql,h1,hunter2\n");
    const { rows, rejected } = toRows(recs);
    expect(rejected).toEqual(["password"]);
    expect(rows[0]).toEqual({ name: "pg-01", engine: "postgresql", host: "h1" });
  });

  it("validates rows on the server, highlights errors in place and registers valid rows", async () => {
    const calls: string[] = [];
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME };
      if (url.pathname === "/api/v1/servers/bulk/validate") {
        calls.push("validate");
        return { body: { rows: [{ index: 0, ok: false, errors: [{ field: "environment", message: "Unknown environment 'Prd'" }], warnings: [] }],
          summary: { total: 1, valid: 0, invalid: 1, warnings: 0 } } };
      }
      if (url.pathname === "/api/v1/servers/bulk/create") { calls.push(String(init.body)); return { body: { results: [], created: 0, failed: 0 } }; }
      return undefined;
    });
    renderWithProviders(<AuthProvider><BulkOnboardingPage /></AuthProvider>);
    const name = await screen.findByLabelText("Row 1 name");
    await userEvent.type(name, "pg-01");
    await userEvent.type(screen.getByLabelText("Row 1 environment"), "Prd");
    await userEvent.click(screen.getByRole("button", { name: "Validate rows" }));
    expect(await screen.findByText("Unknown environment 'Prd'")).toBeInTheDocument();
    expect(screen.getByLabelText("Row 1 environment")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: /Register 0 server/ })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Row 1 environment"), "o");
    expect(screen.queryByText("Unknown environment 'Prd'")).not.toBeInTheDocument();
    expect(calls).toEqual(["validate"]);
  });

  it("rejects password columns in pasted JSON", async () => {
    mockFetch((url) => (url.pathname === "/api/v1/auth/me" ? { body: ME } : undefined));
    renderWithProviders(<AuthProvider><BulkOnboardingPage /></AuthProvider>);
    await userEvent.click(await screen.findByRole("tab", { name: /JSON/ }));
    const ta = screen.getByLabelText("JSON servers");
    await userEvent.click(ta);
    await userEvent.paste('[{"name":"a","engine":"mysql","host":"h","password":"x"}]');
    await userEvent.click(screen.getByRole("button", { name: "Load into grid" }));
    expect(await screen.findByLabelText("Row 1 name")).toHaveValue("a");
    expect(screen.queryByLabelText("Row 1 password")).not.toBeInTheDocument();
    expect(SECRET_MESSAGE).toMatch(/Never put passwords/);
  });
});

describe("identity wizards", () => {
  it("LDAP: requires TLS, never shows the stored secret and runs staged tests", async () => {
    let draft: Record<string, unknown> | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/identity-providers/test-draft") {
        draft = JSON.parse(String(init.body));
        return { body: { ok: false, stages: [
          { stage: "dns", status: "ok", message: "10.1.1.1", duration_ms: 1 }, { stage: "tls", status: "ok", message: "TLS 1.3", duration_ms: 8 },
          { stage: "bind", status: "fail", message: "invalidCredentials", duration_ms: 4 }, { stage: "user_search", status: "skipped", message: null, duration_ms: null },
        ] } };
      }
      return undefined;
    });
    const provider = { id: "p1", kind: "ldap" as const, name: "AD", enabled: true, has_secret: true, mappings: [],
      config: { server_uri: "ldap://dc01:389", start_tls: false, bind_dn: "CN=svc", user_base_dn: "DC=corp", user_filter: "(uid={username})" } };
    renderWithProviders(<LdapWizard provider={provider} />);
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByText("ldap:// needs StartTLS; or use ldaps://")).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(/Use StartTLS/));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    const pw = screen.getByLabelText("Service account password");
    expect(pw).toHaveValue("");
    expect(pw).toHaveAttribute("placeholder", expect.stringContaining("stored"));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await userEvent.click(screen.getByRole("button", { name: /Run test/ }));
    expect(await screen.findByText("invalidCredentials")).toBeInTheDocument();
    expect(screen.getByText("Service account bind").closest("li")).toHaveAttribute("data-status", "fail");
    expect(draft).toMatchObject({ kind: "ldap", provider_id: "p1", config: { start_tls: true } });
    expect(draft).not.toHaveProperty("secret");
  });

  it("OIDC: presets prefill claims and the issuer must be https", async () => {
    mockFetch((url) => url.pathname === "/api/v1/identity-providers/presets" ? { body: [
      { key: "keycloak", label: "Keycloak", issuer_template: "https://kc.example.com/realms/<realm>", scopes: ["openid", "profile", "email"], username_claim: "preferred_username", groups_claim: "groups" },
      { key: "entra", label: "Microsoft Entra ID", issuer_template: "https://login.microsoftonline.com/<tenant>/v2.0", scopes: ["openid", "profile", "email"], username_claim: "upn", groups_claim: "groups" },
    ] } : undefined);
    renderWithProviders(<OidcWizard />);
    await userEvent.click(await screen.findByRole("radio", { name: /Microsoft Entra ID/ }));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await userEvent.type(screen.getByLabelText("Issuer URL"), "http://insecure");
    await userEvent.type(screen.getByLabelText("Client ID"), "abc");
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByText("The issuer must be an https:// URL")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Issuer URL"));
    await userEvent.type(screen.getByLabelText("Issuer URL"), "https://login.microsoftonline.com/t1/v2.0");
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(screen.getByLabelText("Username claim")).toHaveValue("upn");
  });
});

describe("platform setup", () => {
  it("resumes at the step saved on the server", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/auth/me") return { body: ME };
      if (url.pathname === "/api/v1/platform/setup") return { body: { completed: false, needs_setup: true, current_step: "monitoring",
        completed_steps: ["welcome", "admin", "organization", "sites", "environments", "authentication"],
        steps: [], data: { monitoring: { profile: "light" } }, organization: { id: "o1", name: "KBM", timezone: "Asia/Riyadh" } } };
      if (url.pathname === "/api/v1/platform/monitoring-storage") return { body: { retention: { desired_days: 45 } } };
      if (LISTS.includes(url.pathname)) return { body: [] };
      return undefined;
    });
    renderWithProviders(<AuthProvider><SetupWizardPage /></AuthProvider>);
    expect(await screen.findByRole("heading", { name: "Monitoring profile" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Light/ })).toBeChecked();
    expect(screen.getByText("6 of 10 steps done")).toBeInTheDocument();
  });

  it("checklist card lists open items with links", async () => {
    mockFetch((url) => url.pathname === "/api/v1/platform/setup/checklist" ? { body: { done: 1, total: 3, items: [
      { id: "organization", title: "Name your organization", description: "", done: true, href: "/setup", optional: false },
      { id: "admin_password_changed", title: "Change the administrator password", description: "", done: false, href: "#change-password", optional: false },
      { id: "first_server", title: "Register a database server", description: "", done: false, href: "/fleet/servers?add=1", optional: false },
      { id: "authentication", title: "Connect a directory", description: "", done: false, href: "/platform/identity", optional: true },
    ] } } : undefined);
    renderWithProviders(<AuthProvider><SetupChecklist /></AuthProvider>);
    expect(await screen.findByText("1 of 3 done")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Register a database server/ })).toHaveAttribute("href", "/fleet/servers?add=1");
    await userEvent.click(screen.getByRole("button", { name: /Change the administrator password/ }));
    expect(await screen.findByLabelText("Current password")).toBeInTheDocument();
    expect(screen.queryByText("Name your organization")).not.toBeInTheDocument();
  });
});

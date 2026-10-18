import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { ServerDetail } from "@/api/types";
import { RegisterServerDialog } from "@/features/fleet/register-server";
import { DatabaseDriversPage, EngineSupportPage } from "@/features/platform/engines";
import { AdministrationTab } from "@/features/server/tabs/administration";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const ENGINES = [
  { engine: "postgresql", display_name: "PostgreSQL", default_port: 5432, driver: "asyncpg", implementation_phase: 2, implemented: true,
    support_level: "supported", support_note: "", driver_status: "installed", connection_options: [], licensable_features: [], capabilities: {} },
  { engine: "oracle", display_name: "Oracle Database", default_port: 1521, driver: "python-oracledb (thin mode)", implementation_phase: 5, implemented: true,
    support_level: "supported", support_note: "No Diagnostics/Tuning Pack features are used.", driver_status: "installed",
    connection_options: [
      { key: "service_name", label: "Service name", type: "string" },
      { key: "sid", label: "SID", type: "string", help: "Legacy instance SID" },
      { key: "protocol", label: "Protocol", type: "choice", choices: ["tcp", "tcps"] },
    ],
    licensable_features: [{ key: "diagnostics_pack", label: "Oracle Diagnostics Pack", default: false, covers: "AWR, ASH", used_by_db_manager: "Not used in this release" }],
    capabilities: {} },
  { engine: "db2zos", display_name: "IBM Db2 for z/OS", default_port: 446, driver: "ibm_db", implementation_phase: 6, implemented: true,
    support_level: "partial", support_note: "Subsystem performance requires z/OS monitoring tools.", driver_status: "installed",
    connection_options: [], licensable_features: [], capabilities: {} },
];

describe("register server wizard", () => {
  it("shows engine-specific settings, licensing flags off by default and normalized errors", async () => {
    let tested: Record<string, unknown> | null = null;
    mockFetch((url, init) => {
      if (url.pathname === "/api/v1/engines") return { body: ENGINES };
      if (url.pathname === "/api/v1/servers/test-connection") {
        tested = JSON.parse(String(init.body));
        return { body: { ok: false, message: "invalid username/password", error_code: "AUTHENTICATION_FAILED", engine: "oracle" } };
      }
      if (["/api/v1/environments", "/api/v1/sites", "/api/v1/clusters", "/api/v1/applications"].includes(url.pathname)) return { body: [] };
      return undefined;
    });
    renderWithProviders(<RegisterServerDialog open onOpenChange={() => {}} />);
    const engine = await screen.findByLabelText("Engine");
    await screen.findByRole("option", { name: "Oracle Database" });
    expect(screen.getByRole("option", { name: "IBM Db2 for z/OS (partial support)" })).toBeInTheDocument();
    expect(screen.queryByText(/inventory only/i)).not.toBeInTheDocument();
    await userEvent.selectOptions(engine, "oracle");
    expect(screen.getByLabelText("Service name")).toBeInTheDocument();
    expect(screen.getByLabelText("SID")).toBeInTheDocument();
    const lic = screen.getByRole("checkbox", { name: /licensed for Oracle Diagnostics Pack/ });
    expect(lic).not.toBeChecked();
    await userEvent.type(screen.getByLabelText("Host"), "ora01");
    await userEvent.type(screen.getByLabelText("Service name"), "ORCLPDB1");
    await userEvent.type(screen.getByLabelText("SID"), "ORCL");
    await userEvent.type(screen.getByLabelText("Monitoring user"), "DBM_MONITOR");
    await userEvent.click(screen.getByRole("button", { name: /Test connection/ }));
    expect(await screen.findByText(/Authentication failed:/)).toBeInTheDocument();
    expect(tested).toMatchObject({ engine: "oracle", port: 1521, default_database: "ORCLPDB1", connection_options: { sid: "ORCL" }, licensed_features: [] });
  });
});

describe("platform engine pages", () => {
  it("renders the support matrix from backend capability definitions", async () => {
    mockFetch((url) => url.pathname === "/api/v1/platform/engine-support" ? {
      body: {
        rows: ["Connection", "Metrics"],
        engines: [
          { engine: "oracle", display_name: "Oracle Database", implemented: true, support_level: "supported", support_note: "thin", driver: "oracledb",
            driver_status: "installed", engine_specific_rules: 20, generic_rules: 6,
            capabilities: { Connection: { status: "FULL", level: "FULL", note: null, engine_level: "FULL" }, Metrics: { status: "FULL", level: "FULL", note: null, engine_level: "FULL" } },
            licensable_features: [{ key: "tuning_pack", label: "Oracle Tuning Pack", default: false, covers: "SQL Tuning Advisor", used_by_db_manager: "Not used" }] },
          { engine: "db2zos", display_name: "IBM Db2 for z/OS", implemented: true, support_level: "partial", support_note: "external monitoring", driver: "ibm_db",
            driver_status: "installed", engine_specific_rules: 5, generic_rules: 6,
            capabilities: { Connection: { status: "PARTIAL", level: "PARTIAL", note: "Db2 Connect", engine_level: "PARTIAL" }, Metrics: { status: "REQUIRES EXTERNAL MONITORING", level: "REQUIRES_EXTERNAL_MONITORING", note: "IFI", engine_level: "REQUIRES_EXTERNAL_MONITORING" } } },
        ],
      },
    } : undefined);
    renderWithProviders(<EngineSupportPage />);
    expect(await screen.findByText("REQUIRES EXTERNAL MONITORING")).toBeInTheDocument();
    expect(screen.getByText("Partial support")).toBeInTheDocument();
    expect(screen.getAllByText("Disabled by default").length).toBe(1);
  });

  it("shows SQL Server Driver MISSING with the remediation", async () => {
    mockFetch((url) => url.pathname === "/api/v1/platform/drivers" ? {
      body: [
        { key: "pyodbc", driver: "pyodbc", engines: ["mssql"], description: "ODBC bridge", license: "MIT", client_dependency: "msodbcsql18",
          version: "5.3.0", status: "missing", error: "no Microsoft ODBC Driver for SQL Server is registered with unixODBC",
          remediation: "Microsoft ODBC Driver 18 is required. Rebuild the backend image.", details: { odbc_drivers: [] } },
      ],
    } : undefined);
    renderWithProviders(<DatabaseDriversPage />);
    expect(await screen.findByText("SQL Server Driver MISSING")).toBeInTheDocument();
    expect(screen.getByText("MISSING")).toBeInTheDocument();
    expect(screen.getAllByText(/Microsoft ODBC Driver 18 is required/).length).toBeGreaterThan(0);
  });

  it("lists driver health", async () => {
    mockFetch((url) => url.pathname === "/api/v1/platform/drivers" ? {
      body: [
        { key: "pyodbc", driver: "pyodbc", engines: ["mssql"], description: "ODBC bridge", license: "MIT", client_dependency: "msodbcsql18",
          version: "5.3.0", status: "degraded", error: "no Microsoft ODBC Driver for SQL Server is registered", details: { odbc_drivers: [] } },
        { key: "oracledb", driver: "oracledb", engines: ["oracle"], description: "thin", license: "Apache-2.0", client_dependency: null, version: "3.4.0", status: "installed", error: null },
      ],
    } : undefined);
    renderWithProviders(<DatabaseDriversPage />);
    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText(/no Microsoft ODBC Driver/)).toBeInTheDocument();
    expect(screen.getByText("Installed")).toBeInTheDocument();
  });
});

describe("administration tab", () => {
  it("previews engine-generated statements and explains why execution is blocked", async () => {
    mockFetch((url) => {
      if (url.pathname === "/api/v1/servers/s1/admin/actions") return { body: {
        management_mode: "monitoring", available: false, blockers: ["registered as Monitoring only"],
        actions: [{ action: "create_role", label: "Create role", risk: "PRIVILEGED_CHANGE", permission: "user.manage", params: ["name"], allowed: true }],
      } };
      if (url.pathname === "/api/v1/servers/s1/admin/preview") return { body: {
        action: "create_role", risk: "PRIVILEGED_CHANGE", statements: ['CREATE ROLE "REPORTING"'], database: null, notes: [],
        target_object: "REPORTING", confirmation: "CONFIRM", executable: false,
      } };
      return undefined;
    });
    const server = { id: "s1", name: "ora01", engine: "oracle", engine_label: "Oracle", environment: null } as unknown as ServerDetail;
    renderWithProviders(<AdministrationTab server={server} />);
    expect(await screen.findByText("registered as Monitoring only")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Action"), "create_role");
    await userEvent.type(screen.getByLabelText("Name"), "reporting");
    await userEvent.click(screen.getByRole("button", { name: /Preview statements/ }));
    expect(await screen.findByText(/CREATE ROLE "REPORTING"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Execute/ })).toBeDisabled();
  });
});

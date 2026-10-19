import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { AppRoutes } from "@/App";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";

const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [], mfa_enabled: false, mfa_enrollment_required: false,
});

const REPORTS = [
  { key: "fleet-health", title: "Fleet health", description: "Health per server", permission: "server.view", formats: ["csv", "pdf"], params: ["environment_id", "server_id", "engine"], available: true },
  { key: "security-findings", title: "Security findings", description: "Findings by severity", permission: "security.view", formats: ["csv", "pdf"], params: ["environment_id", "server_id", "engine", "statuses", "min_severity"], available: true },
  { key: "query-statistics", title: "Query statistics", description: "Top statements", permission: "query.view", formats: ["csv", "pdf"], params: ["environment_id", "server_id", "engine", "window_hours"], available: false },
];
const SCHEDULE = {
  id: "s1", name: "Weekly security", report_key: "security-findings", report_title: "Security findings", format: "pdf", params: { min_severity: "high" },
  schedule: { frequency: "weekly", time: "07:00", time_zone: "Europe/Paris", weekday: 1, day_of_month: 1 }, channel_ids: ["c1"], owner_id: "u1", owner: "dba",
  enabled: true, next_run_at: "2026-10-26T06:00:00Z", last_run_at: null, last_status: null, last_error: null, created_by: "dba", created_at: "2026-10-19T08:00:00Z",
};

type Call = { path: string; method: string; body: unknown; query: URLSearchParams };
function stub(handler: (path: string, method: string, body: unknown, q: URLSearchParams) => { status?: number; body: unknown } | Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api\/v1/, "");
    calls.push({ path, method, body, query: url.searchParams });
    const r = handler(path, method, body, url.searchParams);
    if (r instanceof Response) return r;
    const out = r ?? { status: 404, body: { error: { code: "not_found", message: "not mocked" } } };
    return new Response(JSON.stringify(out.body), { status: out.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}

function renderApp(route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider><QueryClientProvider client={qc}><MemoryRouter initialEntries={[route]}><AuthProvider><TooltipProvider><AppRoutes /></TooltipProvider></AuthProvider></MemoryRouter></QueryClientProvider><Toaster /></ThemeProvider>,
  );
}

const common = (path: string) => {
  if (path === "/reports") return { body: REPORTS };
  if (path === "/environments") return { body: [{ id: "e1", name: "Production", tier: "production", description: null, sort_order: 1 }] };
  if (path === "/servers") return { body: { items: [{ id: "srv1", name: "PG-PROD-01", engine: "postgresql", environment: { id: "e1", name: "Production", tier: "production" } }], total: 1, page: 1, page_size: 500 } };
  if (path === "/branding/engine-logos") return { body: { engines: [] } };
  return undefined;
};

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("reports catalog", () => {
  it("previews with parameters and downloads the PDF with the same scope", async () => {
    URL.createObjectURL = vi.fn(() => "blob:x") as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    const clicks: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicks.push(this.download); });
    const calls = stub((path) => {
      if (path === "/auth/me") return { body: ME(["reports.view", "server.view", "security.view"]) };
      if (path === "/reports/security-findings/preview") {
        return { body: { key: "security-findings", title: "Security findings", columns: ["server", "severity", "title"], rows: [["PG-PROD-01", "critical", "Superuser login"]], total_rows: 120, scope: ["server PG-PROD-01", "severity high and above"] } };
      }
      if (path === "/reports/security-findings.pdf") return new Response(new Blob(["%PDF-1.4"]), { status: 200, headers: { "Content-Type": "application/pdf" } });
      return common(path);
    });
    renderApp("/reports");
    expect(await screen.findByRole("heading", { name: "Security" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Query statistics \(not available with your role\)/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Security findings" }));
    const dlg = await screen.findByRole("dialog", { name: /Security findings/ });
    await userEvent.selectOptions(within(dlg).getByLabelText("Minimum severity"), "high");
    await waitFor(() => expect(within(dlg).getByRole("option", { name: "PG-PROD-01" })).toBeInTheDocument());
    await userEvent.selectOptions(within(dlg).getByLabelText("Server"), "srv1");
    await userEvent.click(within(dlg).getByRole("checkbox", { name: "resolved" }));
    await userEvent.click(within(dlg).getByRole("button", { name: "Preview" }));
    expect(await within(dlg).findByText("Superuser login")).toBeInTheDocument();
    expect(within(dlg).getByText(/120 rows \(first 1 shown\)/)).toBeInTheDocument();
    const prev = calls.find((c) => c.path === "/reports/security-findings/preview")!;
    expect(prev.query.get("min_severity")).toBe("high");
    expect(prev.query.get("server_id")).toBe("srv1");
    expect(prev.query.getAll("statuses")).toEqual(["resolved"]);
    expect(prev.query.get("window_hours")).toBeNull(); // not a parameter of this report
    await userEvent.click(within(dlg).getByRole("button", { name: "Download Security findings as PDF" }));
    await waitFor(() => expect(clicks).toEqual(["security-findings.pdf"]));
    const pdf = calls.find((c) => c.path === "/reports/security-findings.pdf")!;
    expect(pdf.query.get("min_severity")).toBe("high");
    expect(screen.queryByRole("tab", { name: /Schedules/ })).not.toBeInTheDocument(); // no reports.schedule
  });
});

describe("report schedules", () => {
  it("lists schedules, sends one now, shows its history and creates one with the wizard", async () => {
    let created: unknown = null;
    const calls = stub((path, method, body) => {
      if (path === "/auth/me") return { body: ME(["reports.view", "server.view", "security.view"], ["reports.schedule"]) };
      if (path === "/report-schedules" && method === "GET") return { body: created ? [SCHEDULE, { ...SCHEDULE, id: "s2", name: "Daily fleet", report_key: "fleet-health", report_title: "Fleet health" }] : [SCHEDULE] };
      if (path === "/report-schedules" && method === "POST") { created = body; return { status: 201, body: { ...SCHEDULE, id: "s2", name: "Daily fleet" } }; }
      if (path === "/report-schedules/s1/run") return { body: { id: "r1", schedule_id: "s1", report_key: "security-findings", format: "pdf", status: "success", trigger: "manual", triggered_by: "dba", rows: 12, size_bytes: 20480, deliveries: [{ channel_id: "c1", name: "DBA mail", status: "sent", recipients: 2 }], error: null, started_at: "2026-10-19T08:00:00Z", finished_at: "2026-10-19T08:00:01Z" } };
      if (path === "/report-schedules/s1/runs") return { body: [{ id: "r1", schedule_id: "s1", report_key: "security-findings", format: "pdf", status: "failed", trigger: "schedule", triggered_by: null, rows: null, size_bytes: null, deliveries: [], error: "the owner dba is disabled", started_at: "2026-10-12T05:00:00Z", finished_at: "2026-10-12T05:00:00Z" }] };
      if (path === "/notification-channels") return { body: [{ id: "c1", name: "DBA mail", kind: "email", enabled: true, config: { recipients: ["dba@example.com"] } }, { id: "c2", name: "Teams", kind: "webhook", enabled: true, config: {} }] };
      return common(path);
    });
    renderApp("/reports?tab=schedules");
    await waitFor(() => expect(screen.getByText("Weekly security")).toBeInTheDocument());
    expect(screen.getByText(/Every Monday at 07:00 Europe\/Paris/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Send now/ }));
    expect(await screen.findByText("Weekly security sent (2 recipients)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "History of Weekly security" }));
    const hist = await screen.findByRole("dialog", { name: "History of Weekly security" });
    expect(await within(hist).findByText("the owner dba is disabled")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    await userEvent.click(screen.getAllByRole("button", { name: /New schedule/ })[0]);
    const wiz = await screen.findByRole("dialog", { name: "Schedule a report" });
    await userEvent.selectOptions(within(wiz).getByRole("combobox", { name: "Report" }), "fleet-health");
    await userEvent.type(within(wiz).getByLabelText("Schedule name"), "Daily fleet");
    await userEvent.click(within(wiz).getByRole("button", { name: /Continue/ }));
    await userEvent.click(within(wiz).getByRole("radio", { name: /CSV/ }));
    await userEvent.click(within(wiz).getByRole("button", { name: /Continue/ }));
    await userEvent.selectOptions(within(wiz).getByLabelText("Frequency"), "daily");
    await userEvent.clear(within(wiz).getByLabelText("Time (24 h)"));
    await userEvent.type(within(wiz).getByLabelText("Time (24 h)"), "06:15");
    await userEvent.clear(within(wiz).getByLabelText("Time zone"));
    await userEvent.type(within(wiz).getByLabelText("Time zone"), "UTC");
    await userEvent.click(within(wiz).getByRole("button", { name: /Continue/ }));
    // only e-mail channels are offered
    expect(within(wiz).queryByRole("checkbox", { name: /Teams/ })).not.toBeInTheDocument();
    await userEvent.click(within(wiz).getByRole("button", { name: /Continue/ }));
    expect(await screen.findByText("Choose at least one e-mail channel")).toBeInTheDocument();
    await userEvent.click(within(wiz).getByRole("checkbox", { name: /DBA mail/ }));
    await userEvent.click(within(wiz).getByRole("button", { name: /Continue/ }));
    expect(within(wiz).getByText("Daily at 06:15 UTC")).toBeInTheDocument();
    await userEvent.click(within(wiz).getByRole("button", { name: /Create schedule/ }));
    await waitFor(() => expect(created).not.toBeNull());
    expect(created).toEqual({
      name: "Daily fleet", report_key: "fleet-health", format: "csv", params: {},
      schedule: { frequency: "daily", time: "06:15", time_zone: "UTC", weekday: 1, day_of_month: 1 }, channel_ids: ["c1"], owner_id: null, enabled: true,
    });
    expect(calls.some((c) => c.path === "/users")).toBe(false); // no users.manage: runs as the creator
  });
});

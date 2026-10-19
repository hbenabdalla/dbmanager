import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { AppRoutes } from "@/App";
import { type Backup, encryptionProblem } from "@/features/backup/api";
import { rowActions } from "@/features/backup/pages";
import { policyProblem, toPolicyForm } from "@/features/backup/policies";
import { ServerProtectionPanel } from "@/features/backup/protection";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";

const ME = (perms: string[], platform: string[] = []) => ({
  id: "u1", username: "dba", display_name: "Dana", email: null, auth_source: "local", must_change_password: false,
  permissions: perms, platform_permissions: platform, grants: [], mfa_enabled: true, mfa_enrollment_required: false,
});
type Reply = { status?: number; body: unknown } | undefined;
type Call = { path: string; method: string; body: unknown; query: string };

function stub(handler: (path: string, method: string, body: unknown, url: URL) => Reply) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api\/v1/, "");
    calls.push({ path, method, body, query: url.search });
    const out = handler(path, method, body, url) ?? { status: 404, body: { error: { code: "not_found", message: "not mocked" } } };
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
const last = (calls: Call[], method: string, path: string) => [...calls].reverse().find((c) => c.method === method && c.path === path);

const BK = (over: Partial<Backup> = {}): Backup => ({
  id: "b1b2c3d4-0000-4000-8000-000000000001", kind: "platform", status: "SUCCESS", method: "platform", tool: "DBManager 0.12.0",
  server_id: null, server_name: null, engine: null, database: null, policy_id: null, policy_name: null, target_id: "t1", target_name: "Local",
  job_id: "j0", location: "platform/2026/10/b1/archive.tar.age", location_kind: "target", size_bytes: 20480, sha256: "a".repeat(64),
  encrypted: true, encryption: { mode: "recipient", algorithm: "age v1 (X25519, ChaCha20-Poly1305)", recipient: "age1abc…xyz" },
  app_version: "0.12.0", schema_version: "0010", format_version: "dbmanager-platform/1", verification: { level: "checksum", ok: true },
  verified_at: null, error: null, pinned: false, created_by: "dba", created_at: "2026-10-18T09:00:00Z", finished_at: null, deleted_at: null, ...over,
});
const TARGET = { id: "t1", name: "Local", kind: "local", enabled: true, config: { path: "/var/lib/dbmanager/backups/main" }, has_secret: false, last_check_at: null, last_check_ok: null, last_check_error: null, backups: 1, stored_bytes: 20480, created_by: "dba", created_at: "2026-10-01T00:00:00Z" };
const JOB = (status: string, extra: object = {}) => ({
  id: "j1", kind: "platform_backup", status, trigger: "manual", policy_id: null, target_id: "t1", params: {},
  progress: { step: status === "SUCCESS" ? "done" : "exporting", total: 1, done: status === "SUCCESS" ? 1 : 0 }, result: null, error: null,
  cancel_requested: false, requested_by: "dba", correlation_id: "c", created_at: "2026-10-18T09:00:00Z", started_at: null, finished_at: null, ...extra,
});

describe("backup permissions and validation (pure)", () => {
  it("gates row actions by permission and state", () => {
    const none = () => false;
    const all = () => true;
    expect(rowActions(BK(), none, none)).toEqual({ verify: false, restore: false, remove: false, download: false });
    expect(rowActions(BK(), all, all)).toEqual({ verify: true, restore: true, remove: true, download: true });
    // database restore needs restore.execute + restore.database; platform restore needs platform scope
    const db = BK({ kind: "database", server_id: "s1", server_name: "PG", engine: "postgresql", database: "shop" });
    const dbOps = (p: string) => ["restore.execute", "restore.database", "backup.verify"].includes(p);
    expect(rowActions(db, dbOps, none)).toMatchObject({ restore: true, download: true, remove: false });
    expect(rowActions(BK(), dbOps, none).restore).toBe(false);
    expect(rowActions(BK({ status: "CORRUPTED" }), all, all)).toMatchObject({ restore: false, download: false });
    expect(rowActions(BK({ status: "DELETED" }), all, all)).toMatchObject({ remove: false, verify: false });
    expect(rowActions(BK({ kind: "database", location_kind: "server" }), all, all).download).toBe(false);
  });

  it("requires a passphrase reference for scheduled passphrase backups", () => {
    expect(encryptionProblem({ mode: "passphrase" }, true)).toMatch(/Scheduled passphrase backups need/);
    expect(encryptionProblem({ mode: "passphrase", passphrase_ref: "env:BK_PASS" }, true)).toBeNull();
    expect(encryptionProblem({ mode: "passphrase", passphrase_ref: "plain" }, true)).toMatch(/env:NAME or file/);
    expect(encryptionProblem({ mode: "passphrase" }, false, "short")).toMatch(/12 characters/);
    expect(encryptionProblem({ mode: "recipient", recipient: "ssh-rsa x" }, false)).toMatch(/age1/);
    const f = toPolicyForm(null);
    expect(policyProblem({ ...f, name: "n", target_id: "t1", encryption: { mode: "passphrase" } })).toMatch(/Scheduled passphrase/);
    expect(policyProblem({ ...f, name: "n", target_id: "t1", encryption: { mode: "passphrase" }, schedule: { ...f.schedule, frequency: "manual" } })).toMatch(/Enter a passphrase/);
    expect(policyProblem({ ...f, name: "n", target_id: "t1", kind: "database" })).toMatch(/Select servers/);
    expect(policyProblem({ ...f, name: "n", target_id: "t1", encryption: { mode: "none" } })).toBeNull();
  });
});

describe("backup overview", () => {
  it("shows the last 24 hours and the assets without a recent backup", async () => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["backup.view"], ["backup.view"]) };
      if (path === "/backup/overview") return { body: {
        last_24_hours: { SUCCESS: 3, FAILED: 1 }, last_30_days: { SUCCESS: 40 }, last_platform_backup_at: null,
        platform_protection: { protected: false, last_backup_at: null }, recent_failures: [], stored_bytes: 1024, running_jobs: [],
        next_runs: [], coverage: { protected: 1, at_risk: 1, not_protected: 1 }, servers: 3,
        without_recent_backup: [
          { server_id: "s2", name: "PG-PROD-02", engine: "postgresql", status: "at_risk", last_backup_at: "2026-10-10T00:00:00Z" },
          { server_id: "s3", name: "ORA-PROD-01", engine: "oracle", status: "not_protected", last_backup_at: null },
        ],
      } };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/backups");
    expect(await screen.findByText("3 ok · 1 failed")).toBeInTheDocument();
    const list = await screen.findByTestId("without-recent-backup");
    expect(within(list).getByText("PG-PROD-02")).toBeInTheDocument();
    expect(within(list).getByText("never backed up")).toBeInTheDocument();
    expect(within(list).getByText("Not protected")).toBeInTheDocument();
  });
});

describe("backup history", () => {
  it("deletes only after typing the first 8 characters of the id", async () => {
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME(["backup.view", "backup.verify"], ["backup.view", "backup.delete"]) };
      if (path === "/backups" && method === "GET") return { body: { items: [BK()], total: 1, page: 1, page_size: 50 } };
      if (path.startsWith("/backups/") && method === "DELETE") return { body: { message: "Backup b1b2c3d4 deleted." } };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/backups?tab=history");
    expect(await screen.findByText("DBManager platform")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Restore b1b2c3d4/ })).not.toBeInTheDocument(); // no restore.platform
    expect(screen.getByRole("button", { name: /^Verify b1b2c3d4/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Delete b1b2c3d4/ }));
    const d = await screen.findByRole("dialog", { name: /Delete backup b1b2c3d4/ });
    const confirm = within(d).getByRole("button", { name: "Delete backup" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(d).getByLabelText("Confirmation text"), "b1b2c3d4");
    await userEvent.click(confirm);
    await waitFor(() => expect(last(calls, "DELETE", `/backups/${BK().id}`)?.query).toBe("?confirm=b1b2c3d4"));
  });
});

describe("create backup wizard", () => {
  it("generates a key shown once, runs the job and shows the verified backup", async () => {
    let polls = 0;
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME(["backup.view", "backup.create"], ["backup.view", "backup.create", "backup.policy.manage"]) };
      if (path === "/backup/targets") return { body: [TARGET] };
      if (path === "/backup/keys/generate") return { body: { recipient: "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq", identity: "AGE-SECRET-KEY-1TESTTESTTEST", note: "store it" } };
      if (path === "/backups/platform" && method === "POST") return { status: 202, body: JOB("QUEUED") };
      if (path === "/backup/jobs/j1") {
        polls += 1;
        return { body: polls < 2 ? JOB("RUNNING") : JOB("SUCCESS", { result: { backup_ids: [BK().id], status: "SUCCESS" } }) };
      }
      if (path === `/backups/${BK().id}`) return { body: BK() };
      return undefined;
    });
    providers(<AppRoutes />, "/platform/backups/new");
    const next = () => userEvent.click(screen.getByRole("button", { name: /Continue|Start backup/ }));
    expect(await screen.findByText("Platform configuration")).toBeInTheDocument();
    await next(); // type
    await next(); // scope
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: /Storage target/ }), "t1");
    await next();
    await userEvent.click(await screen.findByRole("button", { name: /Generate key pair/ }));
    expect(await screen.findByTestId("age-identity")).toHaveTextContent("AGE-SECRET-KEY-1TESTTESTTEST");
    await userEvent.click(screen.getByRole("button", { name: /I have saved it/ }));
    expect(screen.queryByTestId("age-identity")).not.toBeInTheDocument(); // shown once
    expect(screen.getByRole("textbox", { name: /Recipient/ })).toHaveValue("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq");
    await next(); // encryption
    expect(screen.getByText(/retention rules never remove them/)).toBeInTheDocument();
    await next(); // retention
    await next(); // review → start
    await waitFor(() => expect(last(calls, "POST", "/backups/platform")).toBeTruthy());
    const sent = last(calls, "POST", "/backups/platform")!.body as { encryption: { mode: string; recipient: string }; target_id: string };
    expect(sent.encryption.mode).toBe("recipient");
    expect(sent.target_id).toBe("t1");
    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled(), { timeout: 6000 });
    await next(); // execute → verification
    expect(await screen.findByText(/checksum/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Verify contents with the key/ })).toBeInTheDocument();
  }, 15000);
});

describe("restore wizard", () => {
  it("previews, requires the typed confirmation and restores the platform", async () => {
    let polls = 0;
    const preview = {
      backup_id: BK().id,
      manifest: { format: "dbmanager-platform/1", created_at: "2026-10-18T09:00:00Z", created_by: "dba", dbm_version: "0.12.0", schema_version: "0010", components: ["alerting", "settings"] },
      integrity: { ok: true, sha256: "a".repeat(64), tables: 5 },
      compatibility: { status: "compatible", notes: [], backup_schema: "0010", current_schema: "0010", backup_version: "0.12.0", current_version: "0.12.0" },
      keys: { required: ["k1"], missing: [] },
      components: [{ key: "alerting", label: "Alert rules…", merge_only: false, selected: true }, { key: "settings", label: "Settings…", merge_only: false, selected: true }],
      impact: { tables: { alert_rules: { in_backup: 3, inserted: 0, updated: 3, deleted: 1 } }, side_effects: { alerts: -2 }, error: null },
      warnings: ["2 row(s) of alerts (not restored) will be removed by cascading deletes."], confirm_phrase: "RESTORE PLATFORM",
    };
    const calls = stub((path, method) => {
      if (path === "/auth/me") return { body: ME(["backup.view"], ["backup.view", "restore.execute", "restore.platform"]) };
      if (path === `/backups/${BK().id}` && method === "GET") return { body: BK() };
      if (path.endsWith("/restore/preview")) return { body: preview };
      if (path.endsWith("/restore") && method === "POST") return { status: 202, body: JOB("QUEUED", { kind: "platform_restore", id: "j1" }) };
      if (path === "/backup/jobs/j1") {
        polls += 1;
        return { body: polls < 2 ? JOB("RUNNING", { kind: "platform_restore" }) : JOB("SUCCESS", { kind: "platform_restore", result: { pre_restore_backup: "99887766-aaaa" }, progress: { steps: [{ step: "restore", status: "ok" }, { step: "validation", status: "ok" }] } }) };
      }
      return undefined;
    });
    providers(<AppRoutes />, `/platform/backups/${BK().id}/restore`);
    const next = () => userEvent.click(screen.getByRole("button", { name: /^(Continue|Restore)$/ }));
    await userEvent.type(await screen.findByLabelText("age identity"), "AGE-SECRET-KEY-1ABC");
    await next(); // select → preview
    expect(await screen.findByText("dbmanager-platform/1")).toBeInTheDocument();
    expect((last(calls, "POST", `/backups/${BK().id}/restore/preview`)!.body as { identity: string }).identity).toBe("AGE-SECRET-KEY-1ABC");
    await next(); // manifest
    expect(screen.getByText(/all 5 tables match/)).toBeInTheDocument();
    await next(); // integrity
    expect(screen.getByText("Compatible")).toBeInTheDocument();
    await next(); // compatibility
    await userEvent.click(screen.getByRole("checkbox", { name: /settings/ })); // only alerting
    await next(); // components → preview again
    expect((last(calls, "POST", `/backups/${BK().id}/restore/preview`)!.body as { components: string[] }).components).toEqual(["alerting"]);
    expect(await screen.findByText(/cascading deletes/, { selector: "p" })).toBeInTheDocument();
    await next(); // impact
    const restore = screen.getByRole("button", { name: "Restore" });
    expect(restore).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Confirmation text"), "RESTORE PLATFORM");
    await userEvent.click(restore);
    await waitFor(() => expect(last(calls, "POST", `/backups/${BK().id}/restore`)).toBeTruthy());
    expect(last(calls, "POST", `/backups/${BK().id}/restore`)!.body).toMatchObject({ confirm: "RESTORE PLATFORM", components: ["alerting"], pre_restore_backup: true });
    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled(), { timeout: 6000 });
    await next(); // restore → validation
    await next(); // validation → result
    expect(await screen.findByText(/Safety backup taken before the restore/)).toBeInTheDocument();
  }, 15000);
});

describe("asset protection panel", () => {
  const caps = { logical: true, native: false, physical: false, pitr: false, restore: true, external: false, status: "AVAILABLE", notes: ["Logical backup with pg_dump"] };
  it.each([
    ["protected", "Protected", false],
    ["at_risk", "At risk", false],
    ["not_protected", "Not protected", true],
  ] as const)("shows %s", async (status, label, configure) => {
    stub((path) => {
      if (path === "/auth/me") return { body: ME(["backup.view"]) };
      if (path === "/servers/s1/backup-protection") {
        return { body: {
          status, last_backup_at: status === "not_protected" ? null : "2026-10-18T09:00:00Z",
          policies: status === "not_protected" ? [] : [{ id: "p1", name: "Nightly", frequency: "daily", retention: { keep_last: 7, keep_days: 0, keep_daily: 0, keep_weekly: 0, keep_monthly: 0 } }],
          capabilities: caps, registration: { protected: true, last_backup_at: "2026-10-18T09:00:00Z" }, recent: [],
        } };
      }
      return undefined;
    });
    providers(<ServerProtectionPanel serverId="s1" />);
    expect(await screen.findByText(label)).toBeInTheDocument();
    if (configure) expect(screen.getByRole("link", { name: "Configure backup" })).toHaveAttribute("href", "/platform/backups?tab=policies&policy=new&server_id=s1");
    else expect(screen.getByText(/Policy: Nightly/)).toBeInTheDocument();
  });
});

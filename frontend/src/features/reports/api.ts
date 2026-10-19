import { ApiError } from "@/api/client";

export type ReportParam = "environment_id" | "server_id" | "engine" | "window_hours" | "statuses" | "min_severity" | "scope";

export interface Report {
  key: string; title: string; description: string; permission: string; formats: string[]; params: ReportParam[]; available: boolean;
}

export interface ReportParams {
  environment_id?: string; server_id?: string; engine?: string; window_hours?: number; statuses?: string[]; min_severity?: string;
  scope?: "server" | "database" | "tablespace";
}

export interface Preview { key: string; title: string; columns: string[]; rows: string[][]; total_rows: number; scope: string[] }

export interface ScheduleSpec { frequency: "daily" | "weekly" | "monthly"; time: string; time_zone: string; weekday: number; day_of_month: number }

export interface ReportSchedule {
  id: string; name: string; report_key: string; report_title: string; format: "csv" | "pdf"; params: ReportParams; schedule: ScheduleSpec;
  channel_ids: string[]; owner_id: string | null; owner: string | null; enabled: boolean; next_run_at: string | null; last_run_at: string | null;
  last_status: string | null; last_error: string | null; created_by: string | null; created_at: string;
}

export interface Delivery { channel_id: string; name: string; status: "sent" | "failed"; recipients?: number; error?: string }

export interface ReportRun {
  id: string; schedule_id: string | null; report_key: string; format: string; status: "success" | "partial" | "failed"; trigger: "schedule" | "manual";
  triggered_by: string | null; rows: number | null; size_bytes: number | null; deliveries: Delivery[]; error: string | null; started_at: string;
  finished_at: string | null;
}

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export const STATUSES = ["open", "acknowledged", "dismissed", "resolved"] as const;
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** Report groups shown on the catalog, in order. */
export const GROUPS: { id: string; title: string; keys: string[] }[] = [
  { id: "fleet", title: "Fleet & capacity", keys: ["fleet-health", "inventory", "capacity", "capacity-forecast"] },
  { id: "performance", title: "Performance", keys: ["performance", "query-statistics", "slow-queries", "query-regressions", "recommendations"] },
  { id: "security", title: "Security", keys: ["security-findings", "security-posture", "privileged-users"] },
  { id: "audit", title: "Audit", keys: ["audit-activity"] },
];

/** Query parameters for a report: only the ones the report understands, no empty values. */
export function queryOf(report: Report | undefined, p: ReportParams): Record<string, string | number | string[] | undefined> {
  const has = (k: ReportParam) => !!report?.params.includes(k);
  return {
    environment_id: has("environment_id") ? p.environment_id || undefined : undefined,
    server_id: has("server_id") ? p.server_id || undefined : undefined,
    engine: has("engine") ? p.engine || undefined : undefined,
    window_hours: has("window_hours") ? p.window_hours : undefined,
    statuses: has("statuses") && p.statuses?.length ? p.statuses : undefined,
    min_severity: has("min_severity") ? p.min_severity || undefined : undefined,
    scope: has("scope") ? p.scope : undefined,
  };
}

/** The same, as the JSON body of a schedule. */
export function paramsBody(report: Report | undefined, p: ReportParams): ReportParams {
  const q = queryOf(report, p);
  return Object.fromEntries(Object.entries(q).filter(([, v]) => v !== undefined)) as ReportParams;
}

export function describeSchedule(s: ScheduleSpec): string {
  const when = `${s.time} ${s.time_zone}`;
  if (s.frequency === "weekly") return `Every ${WEEKDAYS[s.weekday - 1]} at ${when}`;
  if (s.frequency === "monthly") return `Monthly on day ${s.day_of_month} at ${when}`;
  return `Daily at ${when}`;
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return (e as Error)?.message ?? String(e);
}

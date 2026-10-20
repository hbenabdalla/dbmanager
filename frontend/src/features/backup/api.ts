/** Backup & Restore API types (backend: app/api/v1/backups.py). */
import { ApiError } from "@/api/client";

export type BackupStatus = "CREATING" | "VERIFYING" | "SUCCESS" | "FAILED" | "CORRUPTED" | "DELETED";
export type JobStatus = "QUEUED" | "RUNNING" | "VERIFYING" | "SUCCESS" | "PARTIAL_SUCCESS" | "FAILED" | "CANCELLED";
export type EncryptionMode = "none" | "recipient" | "passphrase";

export interface Encryption { mode: EncryptionMode; recipient?: string | null; passphrase_ref?: string | null; algorithm?: string }

export interface Backup {
  id: string; kind: "platform" | "database"; status: BackupStatus; method: string; tool: string | null;
  server_id: string | null; server_name: string | null; engine: string | null; database: string | null;
  policy_id: string | null; policy_name: string | null; target_id: string | null; target_name: string | null; job_id: string | null;
  location: string | null; location_kind: "target" | "server"; size_bytes: number | null; sha256: string | null;
  encrypted: boolean; encryption: Encryption; app_version: string; schema_version: string | null; format_version: string;
  verification: { level?: string; ok?: boolean | null; at?: string; problems?: string[]; note?: string; [k: string]: unknown } | null;
  verified_at: string | null; error: string | null; pinned: boolean; created_by: string | null; created_at: string;
  finished_at: string | null; deleted_at: string | null;
  manifest?: Record<string, unknown>; restore_supported?: boolean; restore_command?: string | null;
}

export interface Asset { key: string; name: string; status: string; backup_id?: string; error?: string | null; server_id?: string; database?: string | null }
export interface Job {
  id: string; kind: string; status: JobStatus; trigger: string; policy_id: string | null; target_id: string | null;
  params: Record<string, unknown>;
  progress: { step?: string; total?: number; done?: number; assets?: Asset[]; steps?: { step: string; status: string; detail?: string | null }[] };
  result: Record<string, unknown> | null; error: string | null; cancel_requested: boolean; requested_by: string | null;
  correlation_id: string | null; created_at: string; started_at: string | null; finished_at: string | null;
}

export interface Target {
  id: string; name: string; kind: "local" | "mounted" | "nfs" | "s3"; enabled: boolean; config: Record<string, unknown>;
  has_secret: boolean; last_check_at: string | null; last_check_ok: boolean | null; last_check_error: string | null;
  backups: number; stored_bytes: number; created_by: string | null; created_at: string;
}

export interface Schedule { frequency: "manual" | "daily" | "weekly" | "monthly"; time: string; time_zone: string; weekday: number; day_of_month: number }
export interface Retention { keep_last: number; keep_days: number; keep_daily: number; keep_weekly: number; keep_monthly: number }
export interface DatabaseScope {
  server_ids: string[]; environment_ids: string[]; site_ids: string[]; tags: string[]; databases: string[];
  server_directory?: string | null; directory?: string | null;
}
export interface PlatformScope { components: string[]; metrics: "none" | "export"; metrics_days: number }

export interface Policy {
  id: string; name: string; description: string | null; kind: "platform" | "database"; enabled: boolean; target_id: string;
  scope: Partial<DatabaseScope & PlatformScope>; schedule: Schedule; encryption: Encryption; retention: Retention;
  channel_ids: string[]; max_age_hours: number; effective_max_age_hours: number | null; last_run_at: string | null;
  next_run_at: string | null; last_status: string | null; last_success_at: string | null; created_by: string | null; created_at: string;
}

export interface Overview {
  last_24_hours?: Record<string, number>;
  without_recent_backup?: { server_id: string; name: string; engine: string; status: "at_risk" | "not_protected"; last_backup_at: string | null }[];
  last_30_days: Record<string, number>; last_platform_backup_at: string | null;
  /** null for grants scoped to environments or servers (platform backups are not theirs to see) */
  platform_protection: { protected: boolean; last_backup_at: string | null } | null;
  recent_failures: Backup[]; stored_bytes: number; running_jobs: Job[];
  next_runs: { policy_id: string; name: string; next_run_at: string; last_status: string | null }[];
  coverage: { protected: number; at_risk: number; not_protected: number }; servers: number;
}

export interface EngineCaps {
  logical: boolean; native: boolean; physical: boolean; pitr: boolean; restore: boolean; external: boolean; status: string; notes: string[];
}
export interface Capabilities {
  engines: Record<string, EngineCaps>; tools: Record<string, { path: string | null; version: string | null }>;
  platform: { components: Record<string, string>; excluded: Record<string, string> };
}

export interface Protection {
  status: "protected" | "at_risk" | "not_protected"; last_backup_at: string | null; freshness_limit_hours?: number | null;
  policies: { id: string; name: string; retention: Retention; frequency: string }[];
  capabilities: EngineCaps; registration: { protected: boolean; last_backup_at: string | null }; recent: Backup[];
}

export interface PlatformPreview {
  backup_id: string;
  manifest: { format: string; created_at: string; created_by: string | null; dbm_version: string; schema_version: string; components: string[]; metrics?: Record<string, unknown> };
  integrity: { ok: boolean; sha256: string; tables: number };
  compatibility: { status: "compatible" | "migration_required" | "unsupported"; notes: string[]; backup_schema: string; current_schema: string; backup_version: string; current_version: string };
  keys: { required: string[]; missing: string[] };
  components: { key: string; label: string; merge_only: boolean; selected: boolean }[];
  impact: { tables: Record<string, { in_backup: number; inserted: number; updated: number; deleted: number }>; side_effects: Record<string, number>; error: string | null } | null;
  warnings: string[]; confirm_phrase: string;
}
export interface DatabasePreview {
  backup_id: string; server: { id: string; name: string; engine: string }; capabilities: EngineCaps; restore_supported: boolean;
  restore_command: string | null; integrity: Backup["verification"]; impact: { creates: string | null; overwrites: null; note: string };
  warnings: string[]; confirm_phrase: string | null;
}

export const PLATFORM_CONFIRM = "RESTORE PLATFORM";
/** app.backup.platform.COMPONENTS order and DEFAULT_COMPONENTS (everything but audit). */
export const COMPONENTS: [string, string][] = [
  ["organization", "Organizations, environments, sites, clusters and applications"],
  ["inventory", "Registered servers, tags, encrypted credential references, discovered databases and accounts"],
  ["identity", "Users, groups, roles, role assignments, identity providers, group mappings, API tokens"],
  ["findings", "Recommendations and security findings with their triage state"],
  ["alerting", "Alert rules, maintenance windows and notification channels"],
  ["settings", "Platform settings and audit forwarding destinations"],
  ["backup_config", "Backup storage targets and policies"],
  ["audit", "Audit log and SQL workspace history (merged on restore, never deleted)"],
];
export const DEFAULT_COMPONENTS = COMPONENTS.map(([k]) => k).filter((k) => k !== "audit");
export const ACTIVE: JobStatus[] = ["QUEUED", "RUNNING", "VERIFYING"];

export const STATUS_TONE: Record<string, "ok" | "warn" | "crit" | "neutral" | "info" | "down"> = {
  SUCCESS: "ok", VERIFYING: "info", CREATING: "info", QUEUED: "neutral", RUNNING: "info", PARTIAL_SUCCESS: "warn",
  FAILED: "crit", CORRUPTED: "crit", CANCELLED: "neutral", DELETED: "neutral",
};
export const COMPAT_LABEL = { compatible: "Compatible", migration_required: "Migration required", unsupported: "Unsupported" } as const;
export const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

/** The UI mirrors the backend check: scheduled passphrase backups need an env:/file: reference. */
export function encryptionProblem(enc: Encryption, scheduled: boolean, runPassphrase?: string): string | null {
  if (enc.mode === "recipient") {
    if (!enc.recipient?.trim()) return "Enter the age recipient (age1…) or generate a key pair";
    if (!/^age1[0-9a-z]{20,}$/.test(enc.recipient.trim())) return "An age recipient starts with age1…";
  }
  if (enc.mode === "passphrase") {
    const ref = enc.passphrase_ref?.trim() ?? "";
    if (ref && !/^(env:[A-Za-z_][A-Za-z0-9_]*|file:\/.+)$/.test(ref)) return "The passphrase reference is env:NAME or file:/path";
    if (scheduled && !ref) return "Scheduled passphrase backups need a passphrase reference (env:NAME or file:/path)";
    if (!scheduled && !ref && !runPassphrase) return "Enter a passphrase (at least 12 characters) or a reference";
    if (runPassphrase && runPassphrase.length < 12) return "The passphrase must have at least 12 characters";
  }
  return null;
}

export function cleanEncryption(enc: Encryption): Encryption {
  if (enc.mode === "recipient") return { mode: "recipient", recipient: enc.recipient?.trim() };
  if (enc.mode === "passphrase") return enc.passphrase_ref?.trim() ? { mode: "passphrase", passphrase_ref: enc.passphrase_ref.trim() } : { mode: "passphrase" };
  return { mode: "none" };
}

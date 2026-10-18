export type Health = "healthy" | "warning" | "critical" | "unavailable" | "unknown";
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type CapabilityLevel =
  | "FULL" | "PARTIAL" | "NOT_SUPPORTED" | "REQUIRES_EXTENSION" | "REQUIRES_LICENSE" | "REQUIRES_PRIVILEGE"
  | "REQUIRES_CONFIGURATION" | "REQUIRES_EXTERNAL_MONITORING" | "NOT_IMPLEMENTED" | "PLANNED";

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Ref { id: string; name: string }
export interface EnvRef extends Ref { tier: string }
export interface Tag { key: string; value: string }
export interface CapabilityStatus { level: CapabilityLevel; note?: string | null }

export interface Grant { role: string; scope_type: string; scope_id: string; scope_label?: string | null; via_group?: string | null }
export interface Me {
  id: string; username: string; display_name: string | null; email: string | null; auth_source: string;
  must_change_password: boolean; permissions: string[]; platform_permissions: string[]; grants: Grant[];
  csrf_token?: string | null;
}

export interface Server {
  id: string; name: string; description: string | null; engine: string; engine_label: string; version: string | null;
  host: string; port: number; default_database: string | null; deployment: string; cloud_provider: string | null;
  environment: EnvRef | null; site: Ref | null; cluster: Ref | null; cluster_role: string | null; application: Ref | null;
  owner: string | null; business_service: string | null; tags: Tag[]; tls_mode: string; monitoring_enabled: boolean;
  collection_interval_s: number; licensed_features: string[]; is_demo: boolean; data_source?: "real" | "simulated"; health_status: Health;
  health_reasons: string[]; connection_status: string; last_error: string | null; last_connected_at: string | null;
  last_collected_at: string | null; circuit_open_until: string | null; key_metrics: Record<string, number | null>;
  database_count: number | null; open_recommendations: number | null; open_findings: number | null;
  implementation: "implemented" | "planned" | "simulated"; created_at: string;
  management_mode?: "monitoring" | "administration"; connection_options?: Record<string, unknown>;
}
export interface CredentialInfo {
  purpose?: string; username?: string; provider?: string; secret_ref?: string | null; has_secret?: boolean;
  rotated_at?: string | null; updated_by?: string | null; configured?: boolean;
}
export interface ServerDetail extends Server {
  capabilities: Record<string, CapabilityStatus>;
  credentials: { monitoring: CredentialInfo | null; admin: CredentialInfo | null; readonly?: CredentialInfo | null };
  collection_status?: Record<string, ComponentStatus>;
  server_info: Record<string, unknown> | null;
  unavailable_metrics: Record<string, string>;
  permissions: string[];
  simulation?: { profile: string; seed: number; condition: string; story: string; run_id: string } | null;
}

export interface MetricPoint { t: string; v: number; max: number }
export interface MetricSeries { server_id: string | null; metric: string; label: string | null; points: MetricPoint[] }
export interface MetricDescriptor { name: string; label: string; unit: string; category: string; description: string }
export interface SeriesResponse {
  start: string; end: string; step_seconds: number; series: MetricSeries[]; catalog?: Record<string, MetricDescriptor>;
  aggregation?: string; servers?: Record<string, { name: string; engine: string; data_source: string }>;
}

export interface Finding {
  id: string; rule_id: string; server_id: string; server_name: string; engine: string; environment: string | null;
  is_demo: boolean; database: string | null; affected_object: string | null; category: string; severity: Severity;
  title: string; finding: string; evidence: Record<string, unknown>; recommendation: string; risk: string;
  confidence: number; guidance: string | null; status: string; status_reason: string | null;
  status_changed_by: string | null; first_seen_at: string; last_seen_at: string; resolved_at: string | null;
  expected_impact?: string; remediation?: string | null;
}

export interface DatabaseInfo {
  name: string; owner: string | null; size_bytes: number | null; encoding: string | null; is_system: boolean;
  accessible: boolean; is_template?: boolean; tablespace?: string | null; connection_limit?: number | null; extra: Record<string, unknown>;
}
export interface SchemaInfo { name: string; owner: string | null; is_system: boolean; object_counts: Record<string, number>; size_bytes: number | null }
export interface ObjectInfo { name: string; schema_name: string; type: string; owner: string | null; size_bytes: number | null; row_estimate: number | null; parent: string | null; extra: Record<string, unknown> }
export interface ObjectDetail {
  object: ObjectInfo; columns: { name: string; data_type: string; nullable: boolean; default: string | null; position: number; comment: string | null }[];
  indexes: Record<string, unknown>[]; constraints: Record<string, unknown>[]; triggers: Record<string, unknown>[];
  statistics: Record<string, unknown>; definition: string | null; definition_note: string | null;
}
export interface DbUser {
  name: string; can_login: boolean; is_superuser: boolean; attributes: Record<string, unknown>; member_of: string[];
  valid_until: string | null; last_login_at: string | null; connection_limit: number | null; is_system: boolean; admin_reasons: string[];
}
export interface Privilege { grantee: string; object_type: string; object_name: string; privilege: string; grantor: string | null; grantable: boolean; via_role: string | null; database: string | null }
export interface SessionInfo {
  session_id: string; username: string | null; database: string | null; application: string | null; client_address: string | null;
  state: string | null; query: string | null; query_start: string | null; backend_start: string | null; xact_start: string | null;
  duration_seconds: number | null; wait_event_type: string | null; wait_event: string | null; blocked_by: string[];
  backend_type: string | null; ssl: boolean | null;
}
export interface LockInfo { session_id: string; lock_type: string; mode: string; granted: boolean; database: string | null; relation: string | null; username: string | null; waiting_seconds: number | null; blocked_by: string[]; query: string | null }
export interface QueryStat {
  query_id: string; database: string | null; username: string | null; query_text: string; calls: number; total_ms: number;
  mean_ms: number; max_ms: number | null; rows: number | null; cpu_ms: number | null; logical_reads: number | null;
  physical_reads: number | null; temp_blocks: number | null; lock_wait_ms: number | null; snapshot_at: string;
  interval?: { seconds: number; calls: number; calls_per_sec: number | null; mean_ms: number | null };
  server_id?: string; server_name?: string; engine?: string; environment?: string | null; is_demo?: boolean;
}
export interface PlanNode {
  id: number; operator: string; detail: string | null; relation: string | null; index: string | null;
  startup_cost: number | null; total_cost: number | null; estimated_rows: number | null; actual_rows: number | null;
  actual_total_ms: number | null; loops: number | null; shared_hit: number | null; shared_read: number | null;
  temp_written: number | null; sort_space_type: string | null; filter: string | null; rows_removed_by_filter: number | null;
  self_cost_pct: number | null; warnings: string[]; children: PlanNode[];
}
export interface PlanIssue { node_id: number; severity: string; code: string; title: string; detail: string }
export interface Classification {
  statement_type: string; risk: "READ" | "LOW_RISK_CHANGE" | "PRIVILEGED_CHANGE" | "DESTRUCTIVE"; read_only: boolean;
  statement_count: number; reasons: string[]; required_permissions: string[]; objects: string[]; parse_ok: boolean;
  explain_analyze: boolean; missing_permissions?: string[]; confirmation?: string | null;
}
export interface ExplainResult {
  engine: string; mode: string; executed: boolean; root: PlanNode | null; issues: PlanIssue[]; total_cost: number | null;
  planning_ms: number | null; execution_ms: number | null; notes: string[]; classification: Classification; simulated: boolean;
  raw?: unknown;
}
export interface ExecResult {
  columns: { name: string; type: string }[]; rows: unknown[][]; row_count: number; truncated: boolean;
  status_message: string | null; duration_ms: number; committed: boolean; classification: Classification;
  max_rows: number; simulated: boolean;
}
export interface AuditEvent {
  id: number; ts: string; user_id: string | null; username: string | null; auth_source: string | null; source_ip: string | null;
  action: string; category: string; risk_class: string | null; target_type: string | null; target_id: string | null;
  server_id: string | null; server_name: string | null; database: string | null; object_name: string | null; result: string;
  failure_reason: string | null; change_reason: string | null; request_id: string | null; details: Record<string, unknown> | null;
}
export interface Role { id: string; name: string; description: string | null; is_system: boolean; permissions: string[] }
export interface Permission { key: string; group: string; description: string; platform_only: boolean }
export interface User {
  id: string; username: string; email: string | null; display_name: string | null; auth_source: string; is_active: boolean;
  must_change_password: boolean; locked_until: string | null; last_login_at: string | null; created_at: string; groups: Ref[];
}
export interface Group { id: string; name: string; description: string | null; members: { id: string; username: string; display_name: string | null }[] }
export interface Assignment {
  id: string; principal_type: "user" | "group"; principal_id: string; principal_name: string | null; role_id: string;
  role_name: string; scope_type: string; scope_id: string; scope_label: string; created_at: string;
}
export interface Environment { id: string; name: string; tier: string; description: string | null; sort_order: number; server_count?: number | null }
export interface Site { id: string; name: string; region: string | null; provider: string | null; server_count?: number | null }
export interface Cluster { id: string; name: string; kind: string; description: string | null; server_count?: number | null }
export interface Application { id: string; name: string; business_service: string | null; owner: string | null; server_count?: number | null }
export interface ConnectionOption {
  key: string; label: string; type: "string" | "boolean" | "integer" | "choice"; choices?: string[]; help?: string;
}
export interface LicensableFeature { key: string; label: string; default: boolean; covers: string; used_by_db_manager: string }
export interface EngineInfo {
  engine: string; display_name: string; default_port: number; driver: string; implementation_phase: number; implemented: boolean;
  support_level?: "supported" | "partial" | "planned"; support_note?: string; driver_status?: string | null;
  driver_detail?: { driver?: string; error: string | null; remediation: string | null } | null;
  connection_options?: ConnectionOption[]; licensable_features?: LicensableFeature[];
  capabilities: Record<string, { engine: CapabilityStatus; adapter: CapabilityStatus }>;
}
export interface ComponentStatus { status: string; message: string | null; at: string }
export interface TestConnectionResult {
  ok: boolean; message: string; error_code?: string | null; engine?: string | null; version?: string | null;
  latency_ms?: number | null; capabilities?: Record<string, CapabilityStatus> | null;
}
export interface IdentityProvider {
  id: string; kind: "ldap" | "oidc" | "saml"; name: string; enabled: boolean; config: Record<string, unknown>; has_secret: boolean;
  mappings: { id: string; external_group: string; group_id: string }[];
}
export interface Job {
  id: string; server_id?: string | null; server_name?: string | null; kind: string; status: string; trigger: string;
  created_at: string; started_at?: string | null; finished_at: string | null; duration_ms: number | null; error: string | null;
  result?: Record<string, unknown> | null; worker?: string | null;
}

/** Wording and tones for capability levels, collection-component states and adapter error codes.
 * Every value comes from the backend (adapter capability definitions and runtime discovery);
 * this module only decides how to present them. */

export type Tone = "ok" | "info" | "warn" | "crit" | "down" | "neutral";

export const LEVEL_LABEL: Record<string, string> = {
  FULL: "Available",
  PARTIAL: "Partial",
  REQUIRES_CONFIGURATION: "Configuration required",
  REQUIRES_EXTENSION: "Configuration required",
  REQUIRES_PRIVILEGE: "Privilege required",
  REQUIRES_LICENSE: "License required",
  REQUIRES_EXTERNAL_MONITORING: "External monitoring",
  NOT_SUPPORTED: "Not supported",
  NOT_IMPLEMENTED: "Not implemented",
  PLANNED: "Not implemented",
};

export const LEVEL_TONE: Record<string, Tone> = {
  FULL: "ok",
  PARTIAL: "info",
  REQUIRES_CONFIGURATION: "warn",
  REQUIRES_EXTENSION: "warn",
  REQUIRES_PRIVILEGE: "warn",
  REQUIRES_LICENSE: "warn",
  REQUIRES_EXTERNAL_MONITORING: "neutral",
  NOT_SUPPORTED: "neutral",
  NOT_IMPLEMENTED: "neutral",
  PLANNED: "neutral",
};

/** Support-matrix statuses ("REQUIRES PRIVILEGE") as sent by /platform/engine-support. */
export function matrixTone(status: string): Tone {
  return LEVEL_TONE[status.replaceAll(" ", "_")] ?? "neutral";
}

export const COMPONENT_LABEL: Record<string, string> = {
  ok: "Collecting",
  partial: "Partial",
  insufficient_privilege: "Insufficient privilege",
  not_supported: "Not supported",
  configuration_required: "Configuration required",
  license_required: "License required",
  failed: "Failed",
  unavailable: "Unavailable",
};
export const COMPONENT_TONE: Record<string, Tone> = {
  ok: "ok",
  partial: "info",
  insufficient_privilege: "warn",
  not_supported: "neutral",
  configuration_required: "warn",
  license_required: "warn",
  failed: "crit",
  unavailable: "down",
};
export const COMPONENT_NAME: Record<string, string> = {
  performance: "Performance metrics",
  query_statistics: "Query statistics",
  inventory: "Inventory discovery",
  recommendations: "Recommendations",
  security: "Security scan",
  capabilities: "Capability discovery",
};

export const ERROR_LABEL: Record<string, string> = {
  CONNECTION_FAILED: "Connection failed",
  AUTHENTICATION_FAILED: "Authentication failed",
  TLS_ERROR: "TLS failure",
  DATABASE_NOT_FOUND: "Database not found",
  INSUFFICIENT_PRIVILEGES: "Insufficient privileges",
  QUERY_TIMEOUT: "Timed out",
  FEATURE_NOT_AVAILABLE: "Feature not available",
  DRIVER_NOT_INSTALLED: "Driver not installed",
  CONFIGURATION_REQUIRED: "Configuration required",
  QUERY_FAILED: "Query failed",
};

export const SUPPORT_LABEL: Record<string, { label: string; tone: Tone }> = {
  supported: { label: "Supported", tone: "ok" },
  partial: { label: "Partial support", tone: "info" },
  planned: { label: "Not implemented", tone: "neutral" },
};

/** Defaults and labels of the "database" field, which means different things per engine. */
export const DATABASE_FIELD: Record<string, { label: string; placeholder: string; value: string; hint?: string }> = {
  postgresql: { label: "Default database", placeholder: "postgres", value: "postgres" },
  mysql: { label: "Default database", placeholder: "optional", value: "" },
  mariadb: { label: "Default database", placeholder: "optional", value: "" },
  mssql: { label: "Database", placeholder: "master", value: "master" },
  oracle: { label: "Service name", placeholder: "ORCLPDB1", value: "", hint: "PDB or CDB service; use the SID option for legacy SID connections" },
  db2: { label: "Database name", placeholder: "SAMPLE", value: "" },
  db2zos: { label: "Location name", placeholder: "DSNLOC1", value: "", hint: "DRDA location of the subsystem (or data sharing group)" },
};

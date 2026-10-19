/**
 * Database engine identity. The marks are DBManager's own (a coloured tile with a monogram),
 * not vendor logos: vendor logos are trademarks whose use needs the owner's permission (see
 * docs/development/brand-assets.md). The name is always available as text next to, or as the
 * accessible name of, the mark — colour never carries the meaning on its own.
 */
export interface EngineMeta {
  /** canonical key used by the API */
  key: string;
  label: string;
  /** monogram printed on the mark */
  short: string;
  /** tile colour (white monogram ≥ 4.5:1) */
  hue: string;
  /** product family: Db2 LUW and Db2 for z/OS share it but stay distinct engines */
  family: string;
}

export const ENGINES: Record<string, EngineMeta> = {
  postgresql: { key: "postgresql", label: "PostgreSQL", short: "Pg", hue: "#35649a", family: "postgresql" },
  mysql: { key: "mysql", label: "MySQL", short: "My", hue: "#9a5a10", family: "mysql" },
  mariadb: { key: "mariadb", label: "MariaDB", short: "Ma", hue: "#7a5230", family: "mysql" },
  mssql: { key: "mssql", label: "SQL Server", short: "SS", hue: "#a4373a", family: "mssql" },
  oracle: { key: "oracle", label: "Oracle", short: "Or", hue: "#b23a20", family: "oracle" },
  db2: { key: "db2", label: "Db2 LUW", short: "D2", hue: "#1f7a72", family: "db2" },
  db2zos: { key: "db2zos", label: "Db2 for z/OS", short: "Dz", hue: "#185a54", family: "db2" },
};

export const UNKNOWN_ENGINE: EngineMeta = { key: "unknown", label: "Unknown engine", short: "?", hue: "#5b6678", family: "unknown" };

const ALIASES: Record<string, string> = {
  postgres: "postgresql", pg: "postgresql", pgsql: "postgresql",
  sqlserver: "mssql", "sql-server": "mssql", sql_server: "mssql", microsoft_sql_server: "mssql",
  "db2-luw": "db2", db2luw: "db2", db2_luw: "db2",
  "db2-zos": "db2zos", db2_zos: "db2zos", "db2-z/os": "db2zos", "db2z/os": "db2zos", zos: "db2zos",
  "oracle-database": "oracle", oracledb: "oracle",
};

/** Canonical engine key for API keys and common spellings (sqlserver, db2-zos, postgres, …). */
export function normalizeEngine(e: string | null | undefined): string {
  const k = (e ?? "").trim().toLowerCase();
  return ENGINES[k] ? k : ALIASES[k] ?? k;
}

export function engineMeta(e: string | null | undefined): EngineMeta {
  const k = normalizeEngine(e);
  if (ENGINES[k]) return ENGINES[k];
  if (!k) return UNKNOWN_ENGINE;
  return { ...UNKNOWN_ENGINE, key: k, label: e ?? k, short: (e ?? "?").replace(/[^a-z0-9]/gi, "").slice(0, 2) || "?" };
}

export function engineLabel(e: string): string {
  return engineMeta(e).label;
}

export const HEALTH_ORDER = ["unavailable", "critical", "warning", "unknown", "healthy"] as const;

export const HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unavailable: "Unavailable",
  unknown: "Not collected",
};

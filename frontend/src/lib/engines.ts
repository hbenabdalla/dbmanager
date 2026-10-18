export const ENGINES: Record<string, { label: string; short: string; hue: string }> = {
  postgresql: { label: "PostgreSQL", short: "PG", hue: "#3d6fb6" },
  mysql: { label: "MySQL", short: "MY", hue: "#c7832c" },
  mariadb: { label: "MariaDB", short: "MA", hue: "#9a6a3c" },
  mssql: { label: "SQL Server", short: "MS", hue: "#b54646" },
  oracle: { label: "Oracle", short: "OR", hue: "#c2412d" },
  db2: { label: "Db2 LUW", short: "D2", hue: "#3b8f8a" },
  db2zos: { label: "Db2 for z/OS", short: "DZ", hue: "#2f6f6b" },
};

export function engineLabel(e: string): string {
  return ENGINES[e]?.label ?? e;
}

export const HEALTH_ORDER = ["unavailable", "critical", "warning", "unknown", "healthy"] as const;

export const HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unavailable: "Unavailable",
  unknown: "Not collected",
};

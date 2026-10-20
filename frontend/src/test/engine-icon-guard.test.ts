/**
 * Guard: every screen that names a database engine uses the central DatabaseEngineIcon, and no
 * screen ships its own engine artwork or colour table. Reads the sources, so a new screen that
 * draws engines differently fails here instead of drifting visually.
 */
const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const src = (rel: string) => {
  const key = `../${rel}`;
  if (!(key in sources)) throw new Error(`missing source ${rel}`);
  return sources[key];
};

// The screens the branding requirement names, by the file that renders them.
const SCREENS: Record<string, string> = {
  Dashboard: "features/dashboard/dashboard.tsx",
  Fleet: "features/fleet/fleet-pages.tsx",
  "Fleet table": "features/fleet/servers.tsx",
  "Server detail": "features/server/server-page.tsx",
  "Database registration": "features/fleet/register-server.tsx",
  "Database onboarding": "features/onboarding/onboarding-wizard.tsx",
  "Environment onboarding": "features/onboarding/bulk.tsx",
  Topology: "features/topology/topology-page.tsx",
  "Search and command palette": "components/command-palette.tsx",
  Alerts: "features/alerts/alerts.tsx",
  Recommendations: "features/shared/findings.tsx",
  "Backup & Restore": "features/backup/pages.tsx",
  "Engine support and database drivers": "features/platform/engines.tsx",
};

describe("engine icon guard", () => {
  it.each(Object.entries(SCREENS))("%s uses DatabaseEngineIcon", (_screen, file) => {
    expect(src(file)).toMatch(/<DatabaseEngineIcon\b/);
  });

  it("no screen embeds engine artwork or its own engine colours", () => {
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.includes("components/engine-icon.tsx") && !path.includes("/test/"))
      .filter(([, code]) =>
        /(postgres(ql)?|mysql|mariadb|oracle|mssql|sqlserver|db2)[\w-]*\.(svg|png)/i.test(code) ||
        /\b(postgresql|mysql|oracle|mssql)\s*:\s*["']#[0-9a-f]{3,6}["']/i.test(code))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });
});

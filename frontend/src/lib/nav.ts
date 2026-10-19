import {
  Activity, BarChart3, BellRing, Database, FileText, Gauge, KeyRound, LayoutGrid, type LucideIcon, Network, ScrollText, Server,
  Settings2, ShieldCheck, Sparkles, SquareTerminal, Users, Wand2,
} from "lucide-react";

export interface NavItem { label: string; to: string; perm?: string | string[]; platform?: boolean }
export interface NavSection { label: string; icon: LucideIcon; to?: string; perm?: string | string[]; items?: NavItem[]; platform?: boolean }
export interface NavGroup { id: string; label: string; sections: NavSection[] }

/** Sidebar: five groups, each collapsible; sections inside a group expand to their pages. */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview", label: "Overview", sections: [
      { label: "Dashboard", icon: LayoutGrid, to: "/" },
      { label: "Topology", icon: Network, to: "/topology", perm: "server.view" },
      {
        label: "Fleet", icon: Server, perm: "server.view", items: [
          { label: "All servers", to: "/fleet/servers" },
          { label: "Clusters", to: "/fleet/clusters" },
          { label: "Databases", to: "/fleet/databases", perm: "database.view" },
          { label: "Environments", to: "/fleet/environments" },
          { label: "Placement", to: "/fleet/placement", perm: "inventory.manage" },
          { label: "Bulk onboarding", to: "/fleet/onboarding/bulk", perm: "server.register" },
        ],
      },
      { label: "Reports", icon: BarChart3, to: "/reports", perm: "reports.view" },
    ],
  },
  {
    id: "operations", label: "Operations", sections: [
      {
        label: "Alerts", icon: BellRing, perm: "alerts.view", items: [
          { label: "Alerts", to: "/alerts" },
          { label: "Alert rules", to: "/alerts/rules" },
          { label: "Maintenance windows", to: "/alerts/maintenance" },
        ],
      },
      {
        label: "Monitoring", icon: Activity, perm: ["performance.view", "session.view"], items: [
          { label: "Overview", to: "/monitoring", perm: "performance.view" },
          { label: "Performance", to: "/monitoring/performance", perm: "performance.view" },
          { label: "Sessions", to: "/monitoring/sessions", perm: "session.view" },
          { label: "Locks", to: "/monitoring/locks", perm: "session.view" },
          { label: "Replication", to: "/monitoring/replication", perm: "performance.view" },
          { label: "Replication topology", to: "/monitoring/replication/topology", perm: "performance.view" },
        ],
      },
      {
        label: "Queries", icon: SquareTerminal, perm: ["query.view", "query.execute", "query.explain"], items: [
          { label: "Query performance", to: "/queries", perm: "query.view" },
          { label: "Slow queries", to: "/queries/slow", perm: "query.view" },
          { label: "Execution plans", to: "/queries/plans", perm: "query.explain" },
          { label: "SQL workspace", to: "/workspace", perm: "query.execute" },
        ],
      },
      {
        label: "Administration", icon: Database, perm: ["database.view", "schema.view", "user.view", "privilege.view"], items: [
          { label: "Databases", to: "/admin/databases", perm: "database.view" },
          { label: "Schemas", to: "/admin/schemas", perm: "schema.view" },
          { label: "Objects", to: "/admin/objects", perm: "schema.view" },
          { label: "Database users", to: "/admin/users", perm: "user.view" },
          { label: "Roles & privileges", to: "/admin/privileges", perm: "privilege.view" },
        ],
      },
    ],
  },
  {
    id: "optimization", label: "Optimization", sections: [
      {
        label: "Recommendations", icon: Sparkles, perm: "performance.view", items: [
          { label: "Performance", to: "/optimization/performance" },
          { label: "Query", to: "/optimization/query" },
          { label: "Configuration", to: "/optimization/configuration" },
        ],
      },
    ],
  },
  {
    id: "security", label: "Security", sections: [
      {
        label: "Security", icon: ShieldCheck, perm: ["security.view", "user.view"], items: [
          { label: "Security posture", to: "/security", perm: "security.view" },
          { label: "Findings", to: "/security/findings", perm: "security.view" },
          { label: "Privileged accounts", to: "/security/privileged", perm: "user.view" },
          { label: "Compliance", to: "/security/compliance", perm: "security.view" },
        ],
      },
      { label: "Audit logs", icon: ScrollText, to: "/audit", perm: "audit.view", platform: true },
    ],
  },
  {
    id: "platform", label: "Platform", sections: [
      {
        label: "Setup", icon: Wand2, perm: "settings.manage", platform: true, items: [
          { label: "Setup wizard", to: "/setup", perm: "settings.manage", platform: true },
          { label: "Setup checklist", to: "/setup/checklist", perm: "settings.manage", platform: true },
        ],
      },
      {
        label: "Configuration", icon: Settings2,
        perm: ["users.manage", "roles.manage", "rbac.manage", "idp.manage", "credentials.manage", "collectors.view", "settings.manage", "simulation.view", "channels.manage", "audit.forward"],
        items: [
          { label: "Users", to: "/platform/users", perm: ["users.manage", "rbac.manage"] },
          { label: "Roles", to: "/platform/roles", perm: ["roles.manage", "rbac.manage"] },
          { label: "Identity providers", to: "/platform/identity", perm: "idp.manage", platform: true },
          { label: "LDAP", to: "/platform/ldap", perm: "idp.manage", platform: true },
          { label: "Credentials", to: "/platform/credentials", perm: "credentials.manage" },
          { label: "Collectors", to: "/platform/collectors", perm: "collectors.view" },
          { label: "Monitoring storage", to: "/platform/monitoring-storage", perm: "collectors.view" },
          { label: "Engine support", to: "/platform/engine-support", perm: "collectors.view" },
          { label: "Database drivers", to: "/platform/drivers", perm: "collectors.view" },
          { label: "Settings", to: "/platform/settings", perm: "settings.manage", platform: true },
          { label: "Simulation", to: "/platform/simulation", perm: "simulation.view", platform: true },
          { label: "Notification channels", to: "/platform/notifications", perm: "channels.manage", platform: true },
          { label: "Audit forwarding", to: "/platform/audit-forwarding", perm: "audit.forward", platform: true },
          { label: "About", to: "/platform/about" },
        ],
      },
    ],
  },
];

/** Flat list of sections (command palette, tests). */
export const NAV: NavSection[] = NAV_GROUPS.flatMap((g) => g.sections);

export const NAV_ICONS = { FileText, Gauge, KeyRound, Users };

export function allowed(perm: string | string[] | undefined, can: (p: string) => boolean): boolean {
  if (!perm) return true;
  return (Array.isArray(perm) ? perm : [perm]).some(can);
}

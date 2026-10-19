import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import { BrandLoading } from "@/components/brand";
import { retryDelay, shouldRetry } from "@/api/client";
import { AppShell } from "@/components/app-shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { EmptyState } from "@/components/ui/states";
import { AuthProvider, useAuth } from "@/lib/auth";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { AdminDatabasesPage, AdminObjectsPage, AdminPrivilegesPage, AdminSchemasPage, AdminUsersPage } from "@/features/admin/admin";
import { AlertRulesPage, AlertsPage, MaintenanceWindowsPage } from "@/features/alerts/alerts";
import { AuditPage } from "@/features/audit/audit";
import { LoginPage } from "@/features/auth/login";
import { DashboardPage } from "@/features/dashboard/dashboard";
import { ClustersPage, EnvironmentsPage, FleetDatabasesPage } from "@/features/fleet/fleet-pages";
import { PlacementPage } from "@/features/fleet/placement";
import { ServersPage } from "@/features/fleet/servers";
import { LocksPage, MonitoringOverviewPage, PerformanceComparePage, ReplicationPage, SessionsPage } from "@/features/monitoring/monitoring";
import { RecommendationsPage } from "@/features/optimization/optimization";
import { AboutPage } from "@/features/platform/about";
import { ApiTokensPage } from "@/features/account/api-tokens";
import { SecurityPage } from "@/features/account/security";
import { NotificationChannelsPage } from "@/features/platform/channels";
import { SiemDestinationsPage } from "@/features/platform/siem";
import { CreateBackupWizard } from "@/features/backup/create-wizard";
import { BackupRestorePage } from "@/features/backup/pages";
import { RestoreWizard } from "@/features/backup/restore-wizard";
import { IdentityProvidersPage } from "@/features/platform/idp";
import { CollectorsPage, CredentialsPage, SettingsPage } from "@/features/platform/ops";
import { MonitoringStoragePage } from "@/features/platform/monitoring-storage";
import { DatabaseDriversPage, EngineSupportPage } from "@/features/platform/engines";
import { RolesPage } from "@/features/platform/roles";
import { SimulationPage } from "@/features/platform/simulation";
import { UsersPage } from "@/features/platform/users";
import { ExecutionPlansPage, QueryPerformancePage, SlowQueriesPage } from "@/features/queries/queries";
import { WorkspacePage } from "@/features/queries/workspace";
import { ReportsPage } from "@/features/reports/reports";
import { CompliancePage, PosturePage, PrivilegedAccountsPage, SecurityFindingsPage } from "@/features/security/security";
import { ServerPage } from "@/features/server/server-page";
import { BulkOnboardingPage } from "@/features/onboarding/bulk";
import { IdentityWizardPage } from "@/features/platform/idp-wizards";
import { SetupChecklistPage } from "@/features/setup/checklist";
import { SetupWizardPage } from "@/features/setup/setup-wizard";
import { TopologyPage } from "@/features/topology/topology-page";
import { ReplicationTopologyPage } from "@/features/topology/replication";
import { CapacityForecastPage, PlanComparePage, QueryComparePage, QueryRegressionsPage, RecommendationValidationPage } from "./features/performance/intelligence";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: shouldRetry,
      retryDelay,
      refetchOnWindowFocus: false,
      staleTime: 10_000,
    },
  },
});

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <BrandLoading />;
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <>{children}</>;
}

function ThemedToaster() {
  const { theme } = useTheme();
  return <Toaster theme={theme} position="bottom-right" richColors closeButton />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<DashboardPage />} />
        <Route path="fleet/servers" element={<ServersPage />} />
        <Route path="fleet/clusters" element={<ClustersPage />} />
        <Route path="fleet/databases" element={<FleetDatabasesPage />} />
        <Route path="fleet/environments" element={<EnvironmentsPage />} />
        <Route path="fleet/placement" element={<PlacementPage />} />
        <Route path="fleet/onboarding/bulk" element={<BulkOnboardingPage />} />
        <Route path="setup" element={<SetupWizardPage />} />
        <Route path="setup/checklist" element={<SetupChecklistPage />} />
        <Route path="platform/identity/wizard" element={<IdentityWizardPage />} />
        <Route path="servers/:id" element={<ServerPage />} />
        <Route path="monitoring" element={<MonitoringOverviewPage />} />
        <Route path="monitoring/performance" element={<PerformanceComparePage />} />
        <Route path="monitoring/capacity" element={<CapacityForecastPage />} />
        <Route path="monitoring/sessions" element={<SessionsPage />} />
        <Route path="monitoring/locks" element={<LocksPage />} />
        <Route path="monitoring/replication" element={<ReplicationPage />} />
        <Route path="monitoring/replication/topology" element={<ReplicationTopologyPage />} />
        <Route path="topology" element={<TopologyPage />} />
        <Route path="fleet/topology" element={<Navigate to="/topology" replace />} />
        <Route path="queries" element={<QueryPerformancePage />} />
        <Route path="queries/slow" element={<SlowQueriesPage />} />
        <Route path="queries/plans" element={<ExecutionPlansPage />} />
        <Route path="queries/plans/compare" element={<PlanComparePage />} />
        <Route path="queries/regressions" element={<QueryRegressionsPage />} />
        <Route path="queries/compare" element={<QueryComparePage />} />
        <Route path="workspace" element={<WorkspacePage />} />
        <Route path="admin/databases" element={<AdminDatabasesPage />} />
        <Route path="admin/schemas" element={<AdminSchemasPage />} />
        <Route path="admin/objects" element={<AdminObjectsPage />} />
        <Route path="admin/users" element={<AdminUsersPage />} />
        <Route path="admin/privileges" element={<AdminPrivilegesPage />} />
        <Route path="optimization/performance" element={<RecommendationsPage group="performance" />} />
        <Route path="optimization/query" element={<RecommendationsPage group="query" />} />
        <Route path="optimization/configuration" element={<RecommendationsPage group="configuration" />} />
        <Route path="optimization/outcomes" element={<RecommendationValidationPage />} />
        <Route path="security" element={<PosturePage />} />
        <Route path="security/findings" element={<SecurityFindingsPage />} />
        <Route path="security/privileged" element={<PrivilegedAccountsPage />} />
        <Route path="security/compliance" element={<CompliancePage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="alerts/rules" element={<AlertRulesPage />} />
        <Route path="alerts/maintenance" element={<MaintenanceWindowsPage />} />
        <Route path="platform/notifications" element={<NotificationChannelsPage />} />
        <Route path="platform/audit-forwarding" element={<SiemDestinationsPage />} />
        <Route path="platform/backups" element={<BackupRestorePage />} />
        <Route path="platform/backups/new" element={<CreateBackupWizard />} />
        <Route path="platform/backups/:id/restore" element={<RestoreWizard />} />
        <Route path="platform/about" element={<AboutPage />} />
        <Route path="account/tokens" element={<ApiTokensPage />} />
        <Route path="account/security" element={<SecurityPage />} />
        <Route path="platform/users" element={<UsersPage />} />
        <Route path="platform/roles" element={<RolesPage />} />
        <Route path="platform/identity" element={<IdentityProvidersPage />} />
        <Route path="platform/ldap" element={<IdentityProvidersPage only="ldap" />} />
        <Route path="platform/credentials" element={<CredentialsPage />} />
        <Route path="platform/collectors" element={<CollectorsPage />} />
        <Route path="platform/monitoring-storage" element={<MonitoringStoragePage />} />
        <Route path="platform/engine-support" element={<EngineSupportPage />} />
        <Route path="platform/drivers" element={<DatabaseDriversPage />} />
        <Route path="platform/settings" element={<SettingsPage />} />
        <Route path="platform/simulation" element={<SimulationPage />} />
        <Route path="*" element={<EmptyState title="Page not found">Check the address, or use the search at the top.</EmptyState>} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <TooltipProvider>
              <AppRoutes />
              <ThemedToaster />
            </TooltipProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

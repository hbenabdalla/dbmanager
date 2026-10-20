import { useQuery } from "@tanstack/react-query";
import { Crown, Server as ServerIcon, TriangleAlert } from "lucide-react";
import { get } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { relTime } from "@/lib/format";

/** GET /platform/instances: every running DBManager process (all replicas). */
export interface DeploymentSummary { api: number; workers: number; stale: number; leader: string | null; versions: string[]; mixed_versions: boolean }
export interface Instance {
  id: string; role: "all" | "api" | "worker"; hostname: string; version: string; git_commit: string | null; runs_worker: boolean;
  is_leader: boolean; started_at: string; last_seen_at: string; status: "active" | "stale"; this_instance: boolean;
}
export interface Instances { instances: Instance[]; summary: DeploymentSummary; heartbeat_seconds: number }

const ROLE: Record<Instance["role"], string> = { all: "API + worker", api: "API", worker: "Worker" };

/** One line for the About page: how many API and worker processes run, and who leads. */
export function deploymentText(d: DeploymentSummary | null | undefined): string {
  if (!d) return "unknown";
  const parts = [`${d.api} API`, `${d.workers} worker${d.workers === 1 ? "" : "s"}`];
  if (d.stale) parts.push(`${d.stale} stale`);
  return parts.join(" · ");
}

/** The deployment's processes, their version and the scheduler leader (Platform → Collectors). */
export function DeploymentCard() {
  const q = useQuery({ queryKey: ["platform-instances"], queryFn: () => get<Instances>("/platform/instances"), refetchInterval: 15_000 });
  if (q.isLoading) return <Card className="mb-4"><CardHeader title="Deployment" /><SkeletonRows rows={2} /></Card>;
  if (q.error || !q.data) return <Card className="mb-4"><CardHeader title="Deployment" /><ErrorState error={q.error} onRetry={() => q.refetch()} className="py-6" /></Card>;
  const { instances, summary } = q.data;
  return (
    <Card className="mb-4" data-testid="deployment-card">
      <CardHeader title="Deployment" description={`${deploymentText(summary)} — every replica reports here every ${q.data.heartbeat_seconds} s`}
        actions={summary.mixed_versions ? <Badge tone="warn"><TriangleAlert className="size-3" />Mixed versions: {summary.versions.join(", ")}</Badge> : undefined} />
      {summary.workers === 0 && <p className="border-b border-border px-4 py-2 text-[13px] text-warn" role="status">No worker is running: nothing is collected or scheduled. Start a worker (DBM_PROCESS_ROLE=worker or all).</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead><tr className="border-b border-border bg-surface-2 text-left text-[12px] text-muted">{["Instance", "Role", "Version", "Started", "Last seen", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
          <tbody>
            {instances.map((i) => (
              <tr key={i.id} className="border-b border-border/60">
                <td className="px-3 py-2"><span className="flex items-center gap-2 font-mono text-[12px]"><ServerIcon className="size-3.5 text-muted" />{i.id}{i.this_instance && <Badge tone="outline">this page</Badge>}</span></td>
                <td className="px-3">{ROLE[i.role] ?? i.role}</td>
                <td className="px-3 tabular">{i.version}{i.git_commit && <span className="ml-1 font-mono text-2xs text-muted">{i.git_commit.slice(0, 7)}</span>}</td>
                <td className="px-3 text-text-2">{relTime(i.started_at)}</td>
                <td className="px-3 text-text-2">{relTime(i.last_seen_at)}</td>
                <td className="px-3 text-right">
                  {i.is_leader && <Badge tone="ok"><Crown className="size-3" />Scheduler leader</Badge>}
                  {i.status === "stale" && <Badge tone="warn">No heartbeat</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

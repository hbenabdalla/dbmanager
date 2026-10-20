import { useQuery } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { Link } from "react-router-dom";
import { get } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { dateTime, relTime } from "@/lib/format";
import type { Protection, Retention } from "./api";
import { StatusBadge } from "./shared";

const retentionText = (r: Retention) =>
  [r.keep_last && `last ${r.keep_last}`, r.keep_days && `${r.keep_days} days`, r.keep_daily && `${r.keep_daily} daily`, r.keep_weekly && `${r.keep_weekly} weekly`, r.keep_monthly && `${r.keep_monthly} monthly`].filter(Boolean).join(", ") || "keep all";

const fmtHours = (h: number) => (h >= 48 && h % 24 === 0 ? `${h / 24} days` : `${h} hours`);

export function ProtectionBadge({ status }: { status: Protection["status"] }) {
  if (status === "protected") return <Badge tone="ok"><ShieldCheck className="size-3" />Protected</Badge>;
  if (status === "at_risk") return <Badge tone="warn"><ShieldAlert className="size-3" />At risk</Badge>;
  return <Badge tone="crit"><ShieldX className="size-3" />Not protected</Badge>;
}

/** Asset protection status on the server page (GET /servers/{id}/backup-protection). */
export function ServerProtectionPanel({ serverId }: { serverId: string }) {
  const q = useQuery({ queryKey: ["backup-protection", serverId], queryFn: () => get<Protection>(`/servers/${serverId}/backup-protection`), retry: false });
  if (q.isLoading) return <Card className="mb-4"><CardHeader title="Backup protection" /><SkeletonRows rows={3} /></Card>;
  if (q.error || !q.data) return <Card className="mb-4"><CardHeader title="Backup protection" /><ErrorState error={q.error} onRetry={() => q.refetch()} className="py-6" /></Card>;
  const p = q.data;
  const pol = p.policies[0];
  return (
    <Card className="mb-4">
      <CardHeader title={<span className="flex items-center gap-2">Backup protection <ProtectionBadge status={p.status} /></span>}
        actions={p.status === "not_protected" ? <Button size="sm" variant="primary" asChild><Link to={`/platform/backups?tab=policies&policy=new&server_id=${serverId}`}>Configure backup</Link></Button> : undefined} />
      <CardBody className="space-y-2 text-[13px]">
        {p.status !== "not_protected" ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>Last backup: {p.last_backup_at ? <span title={dateTime(p.last_backup_at)}>{relTime(p.last_backup_at)}</span> : "never"}</span>
            {pol && <span>Policy: {pol.name}{p.policies.length > 1 ? ` (+${p.policies.length - 1})` : ""} · {pol.frequency}</span>}
            {pol && <span>Retention: {retentionText(pol.retention)}</span>}
            {p.status === "at_risk" && p.freshness_limit_hours != null && <span className="text-warn">No successful backup within {fmtHours(p.freshness_limit_hours)}</span>}
          </div>
        ) : <p>No enabled backup policy covers this server{p.last_backup_at ? `; last manual backup ${relTime(p.last_backup_at)}` : ""}.</p>}
        <p className="text-muted">Engine: {p.capabilities.status.replaceAll("_", " ")} — {p.capabilities.notes[0]}</p>
        <p className="text-muted">Registration metadata: {p.registration.protected ? `covered by the platform backup of ${relTime(p.registration.last_backup_at)}` : "not covered by a platform backup yet"}.</p>
        {p.recent.length > 0 && (
          <ul className="space-y-0.5">
            {p.recent.slice(0, 5).map((b) => <li key={b.id} className="flex gap-2"><StatusBadge status={b.status} /><span>{b.database}</span><span className="text-muted">{relTime(b.created_at)}</span></li>)}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

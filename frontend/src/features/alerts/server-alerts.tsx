import { useQuery } from "@tanstack/react-query";
import { BellRing, CalendarClock } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { get } from "@/api/client";
import type { Page } from "@/api/types";
import { SeverityBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { relTime } from "@/lib/format";
import { AlertDetailDialog, recurrenceLabel, windowWhen, type AlertItem, type MaintenanceWindow } from "./alerts";

export interface ServerAlertCount { active: number; unacknowledged: number; top_severity: string }

/** Per-server active alert counts (fleet list badges); empty when the user cannot view alerts. */
export function useAlertsByServer(enabled = true) {
  return useQuery({
    queryKey: ["alert-summary", "by-server"],
    queryFn: () => get<{ visible: boolean; by_server?: Record<string, ServerAlertCount> }>("/alerts/summary", { by_server: true }),
    refetchInterval: 60_000,
    enabled,
    select: (d) => (d.visible ? d.by_server ?? {} : {}),
  });
}

export function useServerAlerts(serverId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["alerts", "server", serverId],
    queryFn: () => get<Page<AlertItem>>("/alerts", { server_id: serverId, status: ["firing", "acknowledged"], page_size: 50 }),
    refetchInterval: 30_000,
    enabled,
  });
}

export function AlertCountBadge({ count, severity }: { count: number; severity?: string }) {
  if (!count) return null;
  const tone = severity === "critical" || severity === "high" ? "crit" : "warn";
  return <Badge tone={tone}><BellRing className="size-3" /> {count} active alert{count === 1 ? "" : "s"}</Badge>;
}

/** Header badge on the server page. */
export function ServerAlertBadge({ serverId }: { serverId: string }) {
  const { can } = useAuth();
  const q = useServerAlerts(serverId, can("alerts.view"));
  const items = q.data?.items ?? [];
  const top = items.find((a) => a.severity === "critical")?.severity ?? items.find((a) => a.severity === "high")?.severity ?? items[0]?.severity;
  return <AlertCountBadge count={q.data?.total ?? 0} severity={top} />;
}

/** Active alerts and maintenance windows that apply to one server. */
export function ServerAlertsPanel({ serverId }: { serverId: string }) {
  const { can } = useAuth();
  const visible = can("alerts.view");
  const alerts = useServerAlerts(serverId, visible);
  const windows = useQuery({
    queryKey: ["maintenance-windows", "server", serverId],
    queryFn: () => get<Page<MaintenanceWindow>>("/maintenance-windows", { server_id: serverId, page_size: 20 }),
    enabled: visible,
  });
  const [detail, setDetail] = useState<string | null>(null);
  if (!visible) return null;
  const items = alerts.data?.items ?? [];
  const wins = windows.data?.items ?? [];
  if (!items.length && !wins.length) return null;
  return (
    <Card className="mb-4" role="region" aria-label="Alerts and maintenance">
      <CardHeader title="Alerts and maintenance" description="Active alerts on this server and maintenance windows that apply to it" />
      <CardBody className="grid gap-4 text-[13px] lg:grid-cols-2">
        <div>
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-semibold"><BellRing className="size-3.5" /> Active alerts</h3>
          {items.length === 0 ? <p className="text-muted">No active alerts.</p> : (
            <ul className="space-y-1.5">{items.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={a.severity} />
                <button type="button" className="text-left font-medium hover:underline" onClick={() => setDetail(a.id)}>{a.title}</button>
                {a.status === "acknowledged" && <Badge tone="warn">acknowledged</Badge>}
                {a.suppressed && <Badge tone="neutral">suppressed</Badge>}
                <span className="text-2xs text-muted">{relTime(a.fired_at ?? a.pending_since)}</span>
              </li>
            ))}</ul>
          )}
          <Link to="/alerts" className="mt-2 inline-block text-[12.5px] text-accent hover:underline">All alerts</Link>
        </div>
        <div>
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-semibold"><CalendarClock className="size-3.5" /> Maintenance windows</h3>
          {wins.length === 0 ? <p className="text-muted">No maintenance scheduled for this server.</p> : (
            <ul className="space-y-1.5">{wins.map((w) => (
              <li key={w.id}>
                <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{w.name}</span><Badge tone={w.state === "active" ? "warn" : "info"}>{w.state}</Badge></div>
                <div className="text-2xs text-muted">{windowWhen(w)} · {recurrenceLabel(w)}</div>
              </li>
            ))}</ul>
          )}
        </div>
      </CardBody>
      {detail && <AlertDetailDialog alertId={detail} onClose={() => setDetail(null)} />}
    </Card>
  );
}

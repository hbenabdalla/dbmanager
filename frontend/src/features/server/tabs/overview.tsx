import type { ServerDetail } from "@/api/types";
import { MetricChart } from "@/components/charts";
import { KeyValue } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { COMPONENT_LABEL, COMPONENT_NAME, COMPONENT_TONE, LEVEL_LABEL, LEVEL_TONE } from "@/lib/capabilities";
import { bytes, dateTime, duration, metricValue, relTime } from "@/lib/format";
import { pointsFor, useMetrics } from "../hooks";

const TILES: [string, string, string][] = [
  ["connections.total", "Connections", "count"],
  ["connections.utilization", "Connection use", "%"],
  ["connections.active", "Active sessions", "count"],
  ["transactions.per_sec", "Transactions/s", "1/s"],
  ["query.mean_latency_ms", "Mean latency", "ms"],
  ["cache.hit_ratio", "Cache hit ratio", "%"],
  ["sessions.blocked", "Blocked sessions", "count"],
  ["replication.lag_seconds", "Replication lag", "s"],
  ["storage.database_bytes", "Database size", "B"],
  ["cpu.percent", "CPU", "%"],
  ["uptime.seconds", "Uptime", "s"],
];


const ACRONYMS: Record<string, string> = { cpu: "CPU", sql: "SQL", ddl: "DDL" };
export function capLabel(k: string): string {
  const words = k.split("_").map((w) => ACRONYMS[w] ?? w);
  const first = words[0];
  return [first === first.toUpperCase() ? first : first[0].toUpperCase() + first.slice(1), ...words.slice(1)].join(" ");
}

export function OverviewTab({ server }: { server: ServerDetail }) {
  const m = server.key_metrics;
  const charts = useMetrics(server.id, ["connections.total", "transactions.per_sec", "query.mean_latency_ms", "cache.hit_ratio"], { range: "24h" },
    { enabled: server.implementation !== "planned" });
  const lm = (server as unknown as { key_metrics: Record<string, number | null> }).key_metrics;
  const info = (server.server_info ?? {}) as Record<string, unknown>;
  const unavailable = Object.entries(server.unavailable_metrics ?? {});
  const collection = Object.entries(server.collection_status ?? {}).sort();
  const licensing: [string, boolean][] = server.engine === "oracle"
    ? [["Oracle Diagnostics Pack", server.licensed_features.includes("diagnostics_pack")], ["Oracle Tuning Pack", server.licensed_features.includes("tuning_pack")]]
    : [];
  return (
    <div className="space-y-4">
      {server.implementation === "planned" && (
        <Card className="border-info/30 bg-info-soft/40"><CardBody className="text-[13px] text-text-2">
          This server is registered for inventory and ownership. No {server.engine_label} adapter is available, so DBManager doesn't connect to it and shows no metrics rather than estimates.
        </CardBody></Card>
      )}
      <div className="overflow-hidden rounded-[var(--radius-panel)] border border-border bg-surface">
      <div className="-mb-px -mr-px grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
        {TILES.filter(([k]) => k === "uptime.seconds" || m[k] !== undefined).slice(0, 12).map(([k, label, unit]) => {
          const v = k === "uptime.seconds" ? (lm[k] ?? (info.uptime_seconds as number | undefined)) : m[k];
          const reason = server.unavailable_metrics?.[k];
          return (
            <div key={k} className="border-b border-r border-border px-4 py-3">
              <div className="text-[12px] text-muted">{label}</div>
              <div className="tabular mt-0.5 text-[18px] font-semibold">
                {v === null || v === undefined ? <Tooltip content={reason ?? "Not reported by this server"}><span className="text-muted">n/a</span></Tooltip> : metricValue(unit, v)}
              </div>
            </div>
          );
        })}
      </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {[
          ["connections.total", "Connections", "count"], ["transactions.per_sec", "Transactions per second", "1/s"],
          ["query.mean_latency_ms", "Mean statement latency", "ms"], ["cache.hit_ratio", "Cache hit ratio", "%"],
        ].map(([k, t, u]) => (
          <Card key={k}>
            <CardHeader title={t} description="Last 24 hours" />
            <CardBody className="pt-2"><MetricChart height={160} unit={u} series={[{ key: k, label: t, points: pointsFor(charts.data, k) }]} /></CardBody>
          </Card>
        ))}
      </div>

      {(collection.length > 0 || licensing.length > 0) && (
        <Card>
          <CardHeader title="Collection status" description="Each component reports separately: a missing privilege limits that component, not the connection" />
          <CardBody>
            <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {collection.map(([k, c]) => (
                <li key={k} className="flex items-start justify-between gap-3 text-[12.5px]">
                  <span className="min-w-0">
                    <span className="text-text-2">{COMPONENT_NAME[k] ?? k}</span>
                    {c.message && c.status !== "ok" && <span className="block truncate text-muted" title={c.message}>{c.message}</span>}
                  </span>
                  <Tooltip content={`Last result ${relTime(c.at)}`}><span><Badge tone={COMPONENT_TONE[c.status] ?? "neutral"}>{COMPONENT_LABEL[c.status] ?? c.status}</Badge></span></Tooltip>
                </li>
              ))}
              {licensing.map(([label, on]) => (
                <li key={label} className="flex items-center justify-between gap-3 text-[12.5px]">
                  <span className="text-text-2">{label}</span>
                  <Badge tone={on ? "info" : "neutral"}>{on ? "Marked as licensed (not used)" : "Disabled"}</Badge>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <Card>
          <CardHeader title="Server" />
          <CardBody>
            <KeyValue items={[
              ["Version", (info.extra as Record<string, string> | undefined)?.version_full ?? server.version],
              ["Endpoint", <span className="font-mono text-[12.5px]">{server.host}:{server.port}</span>],
              ["Default database", server.default_database],
              ["Deployment", server.deployment + (server.cloud_provider ? ` (${server.cloud_provider})` : "")],
              ["Role", info.is_replica === true ? "Replica" : info.is_replica === false ? "Primary / standalone" : server.cluster_role],
              ["Started", info.started_at ? `${dateTime(String(info.started_at))} (${duration(Number(info.uptime_seconds))} ago)` : null],
              ["Connected as", info.current_user as string],
              ["TLS mode", server.tls_mode],
              ["Owner", server.owner],
              ["Application", server.application?.name],
              ["Tags", server.tags.length ? <span className="flex flex-wrap gap-1">{server.tags.map((t) => <Badge key={t.key} tone="outline">{t.key}{t.value ? `=${t.value}` : ""}</Badge>)}</span> : null],
              ["Last connected", relTime(server.last_connected_at)],
              ["Last error", server.last_error ? <span className="text-crit">{server.last_error}</span> : null],
              ["Size", bytes(m["storage.database_bytes"])],
            ]} />
            {unavailable.length > 0 && (
              <div className="mt-4 rounded-md border border-border bg-surface-2 p-3 text-[12.5px]">
                <div className="mb-1 font-medium">Not collected for this server</div>
                <ul className="space-y-0.5 text-muted">{unavailable.map(([k, v]) => <li key={k}><span className="font-mono text-2xs">{k}</span>: {v}</li>)}</ul>
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Capabilities" description="What DBManager can do on this server, from runtime discovery" />
          <CardBody>
            <ul className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
              {Object.entries(server.capabilities).sort().map(([k, c]) => (
                <li key={k} className="flex items-center justify-between gap-2 text-[12.5px]">
                  <span className="truncate text-text-2">{capLabel(k)}</span>
                  <Tooltip content={c.note}><span><Badge tone={LEVEL_TONE[c.level] ?? "neutral"}>{LEVEL_LABEL[c.level] ?? c.level}</Badge></span></Tooltip>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

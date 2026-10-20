import { ServerReplicationTopology } from "@/features/topology/replication";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { get } from "@/api/client";
import type { Job, Page, ServerDetail } from "@/api/types";
import { Meter } from "@/components/charts";
import { KeyValue } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { bytes, compactNum, dateTime, duration, ms, relTime } from "@/lib/format";

interface Replication { role: string; replicas: Record<string, unknown>[]; upstream: Record<string, unknown> | null; lag_seconds: number | null; slots: Record<string, unknown>[]; notes: string[] }

export function ReplicationTab({ server }: { server: ServerDetail }) {
  const q = useQuery({ queryKey: ["replication", server.id], queryFn: () => get<Replication>(`/servers/${server.id}/replication`), refetchInterval: 15_000 });
  if (q.isLoading) return <SkeletonRows />;
  if (q.error) return <ErrorState error={q.error} />;
  const r = q.data!;
  const topo = <div className="lg:col-span-2"><ServerReplicationTopology server={server} height={300} /></div>;
  const lagTone = r.lag_seconds === null ? undefined : r.lag_seconds > 300 ? "var(--crit)" : r.lag_seconds > 60 ? "var(--warn)" : "var(--ok)";
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      {r.role !== "standalone" && topo}
      <Card>
        <CardHeader title="Role" />
        <CardBody className="space-y-3">
          <div className="text-[22px] font-semibold capitalize">{r.role}</div>
          <KeyValue items={[["Lag", r.lag_seconds === null ? "—" : <span style={{ color: lagTone }} className="font-medium">{duration(r.lag_seconds)}</span>], ["Replicas", r.replicas.length], ["Slots", r.slots.length]]} />
          {r.upstream && <KeyValue items={Object.entries(r.upstream).map(([k, v]) => [k.replaceAll("_", " "), String(v ?? "—")])} />}
          {r.notes.map((n) => <p key={n} className="text-[12.5px] text-muted">{n}</p>)}
        </CardBody>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader title="Connected replicas" />
          {r.replicas.length === 0 ? <EmptyState title={r.role === "replica" ? "This server is a replica" : "No replicas connected"} /> : (
            <table className="w-full text-[13px]"><thead><tr className="border-b border-border bg-surface-2 text-left text-[12px] text-muted">{["Name", "Address", "State", "Sync", "Replay lag"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
              <tbody>{r.replicas.map((x, i) => <tr key={i} className="border-b border-border/60"><td className="px-3 py-2 font-medium">{String(x.application_name ?? "—")}</td><td className="px-3 font-mono text-[12px]">{String(x.client_addr ?? "")}</td><td className="px-3">{String(x.state ?? "")}</td><td className="px-3">{String(x.sync_state ?? "")}</td><td className="px-3 tabular">{duration(x.replay_lag_s as number)}</td></tr>)}</tbody></table>
          )}
        </Card>
        {r.slots.length > 0 && (
          <Card>
            <CardHeader title="Replication slots" description="Inactive slots retain WAL and can fill the disk" />
            <table className="w-full text-[13px]"><tbody>{r.slots.map((s, i) => (
              <tr key={i} className="border-b border-border/60"><td className="px-3 py-2 font-medium">{String(s.slot_name)}</td><td className="px-3">{String(s.slot_type)}</td><td className="px-3">{s.active ? <Badge tone="ok">active</Badge> : <Badge tone="warn">inactive</Badge>}</td><td className="px-3 text-right tabular">{bytes(s.retained_bytes as number)}</td></tr>
            ))}</tbody></table>
          </Card>
        )}
      </div>
    </div>
  );
}

const STORAGE_EXTRA: Record<string, string> = {
  log_used_bytes: "Transaction log used",
  log_available_bytes: "Transaction log available",
  in_use_bytes: "Temporary space in use",
  redo_log_bytes: "Online redo logs",
};

interface Storage { total_bytes: number | null; databases: { name: string; size_bytes: number | null }[]; tablespaces: { name: string; size_bytes?: number | null; used_bytes?: number | null; used_pct?: number; owner?: string; autoextend?: boolean }[]; top_tables: Record<string, unknown>[]; top_indexes: Record<string, unknown>[]; temp: Record<string, unknown>; notes: string[] }

export function StorageTab({ server }: { server: ServerDetail }) {
  const q = useQuery({ queryKey: ["storage", server.id], queryFn: () => get<Storage>(`/servers/${server.id}/storage`) });
  if (q.isLoading) return <SkeletonRows />;
  if (q.error) return <ErrorState error={q.error} />;
  const s = q.data!;
  const maxDb = Math.max(1, ...s.databases.map((d) => d.size_bytes ?? 0));
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader title="Databases" description={`Total ${bytes(s.total_bytes)}`} />
        <CardBody className="space-y-2.5">
          {s.databases.sort((a, b) => (b.size_bytes ?? 0) - (a.size_bytes ?? 0)).map((d) => (
            <div key={d.name}><div className="mb-1 flex justify-between text-[13px]"><span>{d.name}</span><span className="tabular text-text-2">{bytes(d.size_bytes)}</span></div><Meter value={d.size_bytes ?? 0} max={maxDb} tone="var(--chart-4)" /></div>
          ))}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Tablespaces" />
        <CardBody className="space-y-2.5">
          {s.tablespaces.map((t) => (
            <div key={t.name}>
              <div className="mb-1 flex justify-between text-[13px]"><span>{t.name}{t.autoextend === false && <Badge tone="warn" className="ml-2">no autoextend</Badge>}</span><span className="tabular text-text-2">{bytes(t.size_bytes ?? t.used_bytes ?? null)}{t.used_pct != null ? `, ${t.used_pct}% used` : ""}</span></div>
              {t.used_pct != null && <Meter value={t.used_pct} />}
            </div>
          ))}
          {s.temp && "temp_bytes" in s.temp && <p className="pt-2 text-[12.5px] text-muted">Temporary files written since stats reset: {bytes(s.temp.temp_bytes as number)}</p>}
          {s.temp && !("temp_bytes" in s.temp) && Object.entries(s.temp).filter(([k, v]) => k.endsWith("_bytes") && typeof v === "number").map(([k, v]) => <p key={k} className="pt-1 text-[12.5px] text-muted">{STORAGE_EXTRA[k] ?? k.replace(/_bytes$/, "").replace(/_/g, " ")}: {bytes(v as number)}</p>)}
        </CardBody>
      </Card>
      <Card className="xl:col-span-2">
        <CardHeader title="Largest tables" description={s.notes.join(" ")} />
        <table className="w-full text-[13px]"><thead><tr className="border-b border-border bg-surface-2 text-left text-[12px] text-muted">{["Table", "Rows (est.)", "Table", "Indexes", "Total"].map((h, i) => <th key={i} className={`px-3 py-2 font-medium ${i ? "text-right" : ""}`}>{h}</th>)}</tr></thead>
          <tbody>{s.top_tables.map((t, i) => <tr key={i} className="border-b border-border/60"><td className="px-3 py-2 font-medium">{String(t.schema)}.{String(t.name)}</td><td className="px-3 text-right tabular">{compactNum(t.row_estimate as number)}</td><td className="px-3 text-right tabular">{bytes(t.table_bytes as number)}</td><td className="px-3 text-right tabular">{bytes(t.index_bytes as number)}</td><td className="px-3 text-right font-medium tabular">{bytes(t.total_bytes as number)}</td></tr>)}</tbody></table>
      </Card>
    </div>
  );
}

interface Param { name: string; value: string | null; unit: string | null; category: string | null; description: string | null; source: string | null; default_value: string | null; requires_restart: boolean | null; pending_restart: boolean | null; is_default: boolean | null }

export function ConfigurationTab({ server }: { server: ServerDetail }) {
  const q = useQuery({ queryKey: ["config", server.id], queryFn: () => get<Param[]>(`/servers/${server.id}/configuration`) });
  const [f, setF] = useState("");
  const [changed, setChanged] = useState(false);
  const rows = useMemo(() => (q.data ?? []).filter((p) => (!changed || p.is_default === false) && (!f || `${p.name} ${p.category} ${p.description}`.toLowerCase().includes(f.toLowerCase()))), [q.data, f, changed]);
  const columns: ColumnDef<Param, unknown>[] = [
    { header: "Parameter", accessorKey: "name", cell: ({ row: { original: p } }) => <div><div className="font-mono text-[12.5px] font-medium">{p.name}</div><div className="max-w-[420px] truncate text-[12px] text-muted">{p.description}</div></div> },
    { header: "Value", accessorKey: "value", cell: ({ row: { original: p } }) => <span className="font-mono text-[12.5px]">{p.value}{p.unit ? <span className="text-muted"> {p.unit}</span> : null}</span> },
    { header: "Default", accessorKey: "default_value", cell: ({ getValue }) => <span className="font-mono text-[12px] text-muted">{(getValue() as string) ?? ""}</span> },
    { header: "Source", accessorKey: "source" },
    { header: "Category", accessorKey: "category", cell: ({ getValue }) => <span className="text-[12px] text-text-2">{getValue() as string}</span> },
    { header: "", id: "flags", enableSorting: false, cell: ({ row: { original: p } }) => <span className="flex gap-1">{p.requires_restart && <Badge tone="outline">restart</Badge>}{p.pending_restart && <Badge tone="warn">pending restart</Badge>}</span> },
  ];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b border-border p-3 text-[13px]">
        <Input aria-label="Filter parameters" className="w-72" placeholder="Parameter, category, description" value={f} onChange={(e) => setF(e.target.value)} />
        <label className="flex items-center gap-2"><input type="checkbox" checked={changed} onChange={(e) => setChanged(e.target.checked)} /> Only non-default values</label>
        <span className="ml-auto text-[12px] text-muted">Read only. Configuration changes go through the SQL workspace with confirmation, or your provider's parameter groups.</span>
      </div>
      <DataTable columns={columns} data={rows} loading={q.isLoading} error={q.error} dense pageSize={100} />
    </Card>
  );
}

export function LogsTab({ server }: { server: ServerDetail }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const q = useQuery({ queryKey: ["logs", server.id, page], queryFn: () => get<Page<Job>>(`/servers/${server.id}/logs`, { page, page_size: 50 }), refetchInterval: 20_000, placeholderData: (p) => p });
  const items = (q.data?.items ?? []).filter((j) => !status || j.status === status);
  const columns: ColumnDef<Job, unknown>[] = [
    { header: "Time", accessorKey: "created_at", cell: ({ getValue }) => <span className="text-[12.5px]">{dateTime(getValue() as string)}</span> },
    { header: "Job", accessorKey: "kind", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
    { header: "Status", accessorKey: "status", cell: ({ getValue }) => { const v = getValue() as string; return <Badge tone={v === "done" ? "ok" : v === "failed" ? "crit" : v === "running" ? "info" : "neutral"}>{v}</Badge>; } },
    { header: "Trigger", accessorKey: "trigger" },
    { header: "Duration", accessorKey: "duration_ms", meta: { align: "right" }, cell: ({ getValue }) => ms(getValue() as number) },
    { header: "Result", id: "result", enableSorting: false, cell: ({ row: { original: j } }) => <span className={`text-[12px] ${j.error ? "text-crit" : "text-muted"}`}>{j.error ?? (j.result ? Object.entries(j.result).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ") : "")}</span> },
  ];
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-border p-3 text-[13px]">
        <Select aria-label="Status" className="w-40" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option>{["done", "failed", "skipped", "running", "pending"].map((s) => <option key={s}>{s}</option>)}</Select>
        <span className="text-[12px] text-muted">Collection log for this server. Database server log files are not read by this release.</span>
        <span className="ml-auto text-[12px] text-muted">Updated {relTime(q.dataUpdatedAt ? new Date(q.dataUpdatedAt).toISOString() : null)}</span>
      </div>
      <DataTable columns={columns} data={items} loading={q.isLoading} error={q.error} dense manual={q.data ? { page, pageSize: 50, total: q.data.total, onPage: setPage } : undefined} />
    </Card>
  );
}

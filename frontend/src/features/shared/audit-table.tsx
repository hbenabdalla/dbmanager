import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Download } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { download, get } from "@/api/client";
import type { AuditEvent, Page } from "@/api/types";
import { RiskBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Input, Select } from "@/components/ui/input";
import { KeyValue, CodeBlock } from "@/components/page";
import { useAuth } from "@/lib/auth";
import { dateTime } from "@/lib/format";

const RESULT_TONE: Record<string, "ok" | "crit" | "warn"> = { success: "ok", failure: "crit", denied: "warn" };

export function AuditTable({ serverId, serverName }: { serverId?: string; serverName?: string }) {
  const { canPlatform } = useAuth();
  const [page, setPage] = useState(1);
  const [f, setF] = useState({ q: "", username: "", action: "", result: "", category: "", start: "", end: "" });
  const [sel, setSel] = useState<AuditEvent | null>(null);
  const query = {
    q: f.q || undefined, username: f.username || undefined, action: f.action || undefined,
    result: f.result ? [f.result] : undefined, category: f.category ? [f.category] : undefined,
    start: f.start ? new Date(f.start).toISOString() : undefined, end: f.end ? new Date(f.end).toISOString() : undefined,
  };
  const data = useQuery({
    queryKey: ["audit", serverId, f, page],
    queryFn: () => serverId ? get<Page<AuditEvent>>(`/servers/${serverId}/audit`, { page, page_size: 50 }) : get<Page<AuditEvent>>("/audit", { ...query, page, page_size: 50 }),
    placeholderData: (p) => p,
  });
  const set = (k: keyof typeof f, v: string) => { setF((p) => ({ ...p, [k]: v })); setPage(1); };
  const exportCsv = async () => {
    try {
      await download("/audit/export.csv", "audit-log.csv", { query: { ...query, server: serverName } });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const columns: ColumnDef<AuditEvent, unknown>[] = [
    { header: "Time", accessorKey: "ts", cell: ({ getValue }) => <span className="whitespace-nowrap text-[12.5px]">{dateTime(getValue() as string)}</span> },
    { header: "User", accessorKey: "username", cell: ({ row: { original: e } }) => <div><div className="font-medium">{e.username ?? "system"}</div><div className="text-2xs text-muted">{e.auth_source}{e.source_ip ? ` from ${e.source_ip}` : ""}</div></div> },
    { header: "Action", accessorKey: "action", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
    { header: "Target", id: "target", cell: ({ row: { original: e } }) => <span className="text-[12.5px]">{[e.server_name, e.database, e.object_name].filter(Boolean).join(" / ") || <span className="text-muted">—</span>}</span> },
    { header: "Risk", accessorKey: "risk_class", cell: ({ getValue }) => getValue() ? <RiskBadge risk={getValue() as string} /> : null },
    { header: "Result", accessorKey: "result", cell: ({ row: { original: e } }) => <div><Badge tone={RESULT_TONE[e.result] ?? "neutral"}>{e.result}</Badge>{e.failure_reason && <div className="mt-0.5 max-w-[260px] truncate text-2xs text-muted">{e.failure_reason}</div>}</div> },
  ];
  return (
    <Card>
      {!serverId && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <Input aria-label="Search" className="w-56" placeholder="Search action, object, request id" value={f.q} onChange={(e) => set("q", e.target.value)} />
          <Input aria-label="User" className="w-36" placeholder="User" value={f.username} onChange={(e) => set("username", e.target.value)} />
          <Input aria-label="Action prefix" className="w-40" placeholder="Action, e.g. workspace" value={f.action} onChange={(e) => set("action", e.target.value)} />
          <Select aria-label="Result" className="w-32" value={f.result} onChange={(e) => set("result", e.target.value)}><option value="">Any result</option><option>success</option><option>failure</option><option>denied</option></Select>
          <Select aria-label="Category" className="w-36" value={f.category} onChange={(e) => set("category", e.target.value)}><option value="">Any category</option>{["auth", "admin", "query", "security", "collector", "platform", "report", "audit"].map((c) => <option key={c}>{c}</option>)}</Select>
          <Input type="datetime-local" aria-label="From" className="w-[180px]" value={f.start} onChange={(e) => set("start", e.target.value)} />
          <Input type="datetime-local" aria-label="To" className="w-[180px]" value={f.end} onChange={(e) => set("end", e.target.value)} />
          {canPlatform("audit.export") && <Button className="ml-auto" size="sm" onClick={exportCsv}><Download /> Export CSV</Button>}
        </div>
      )}
      <DataTable columns={columns} data={data.data?.items} loading={data.isLoading} error={data.error} dense onRowClick={setSel}
        manual={data.data ? { page, pageSize: 50, total: data.data.total, onPage: setPage } : undefined} />
      {sel && (
        <Dialog open onOpenChange={(o) => !o && setSel(null)} title={sel.action} description={dateTime(sel.ts)} width="max-w-xl">
          <KeyValue items={[
            ["User", sel.username ?? "system"], ["Source", `${sel.auth_source ?? ""} ${sel.source_ip ?? ""}`], ["Result", sel.result],
            ["Failure reason", sel.failure_reason], ["Change reason", sel.change_reason], ["Risk class", sel.risk_class], ["Server", sel.server_name],
            ["Database", sel.database], ["Object", sel.object_name], ["Target", sel.target_type ? `${sel.target_type} ${sel.target_id ?? ""}` : null],
            ["Request id", <span className="font-mono text-[12px]">{sel.request_id}</span>],
          ]} />
          {sel.details && <div className="mt-3"><CodeBlock code={JSON.stringify(sel.details, null, 2)} maxHeight="16rem" /></div>}
        </Dialog>
      )}
    </Card>
  );
}

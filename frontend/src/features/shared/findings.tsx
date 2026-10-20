import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, get, patch } from "@/api/client";
import type { Finding, Page } from "@/api/types";
import { CodeBlock } from "@/components/page";
import { DatabaseEngineIcon, SeverityBadge, SimulatedBadge } from "@/components/status";
import { useSourceParam } from "@/lib/fleet-source";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { dateTime, relTime } from "@/lib/format";
import { ValidationBadge, ValidationDetail } from "../performance/intelligence";

type Kind = "recommendations" | "security";

function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence);
  if (!entries.length) return <p className="text-[13px] text-muted">No measured values attached.</p>;
  return (
    <dl className="grid grid-cols-[minmax(140px,max-content)_1fr] gap-x-4 gap-y-1 rounded-md border border-border bg-surface-2 p-3 text-[12.5px]">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k.replaceAll("_", " ")}</dt>
          <dd className="min-w-0 break-words font-mono text-[12px]">{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function FindingDialog({ f, kind, onClose }: { f: Finding; kind: Kind; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const perm = kind === "security" ? "security.manage" : "performance.tune";
  const path = kind === "security" ? `/security/findings/${f.id}` : `/recommendations/${f.id}`;
  const m = useMutation({
    mutationFn: (status: string) => patch<Finding>(path, { status, reason: reason || undefined }),
    onSuccess: (r) => { toast.success(`Marked ${r.status}`); qc.invalidateQueries({ queryKey: [kind] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); onClose(); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl"
      title={<span className="flex items-center gap-2"><SeverityBadge severity={f.severity} /> {f.title}</span>}
      description={<span className="flex flex-wrap items-center gap-x-3 gap-y-1"><Link className="hover:underline" to={`/servers/${f.server_id}`}>{f.server_name}</Link><span className="font-mono">{f.rule_id}</span><span>{f.category}</span>{f.is_demo && <SimulatedBadge />}</span>}
      footer={can(perm) ? (
        <>
          <Button variant="ghost" onClick={onClose}>Close</Button>
          {f.status !== "acknowledged" && <Button onClick={() => m.mutate("acknowledged")} disabled={m.isPending}>Acknowledge</Button>}
          {f.status !== "dismissed" && <Button onClick={() => m.mutate("dismissed")} disabled={m.isPending}>Dismiss</Button>}
          {f.status !== "resolved" && <Button variant="primary" onClick={() => m.mutate("resolved")} disabled={m.isPending}>Mark resolved</Button>}
          {f.status !== "open" && <Button onClick={() => m.mutate("open")} disabled={m.isPending}>Reopen</Button>}
        </>
      ) : <Button onClick={onClose}>Close</Button>}>
      <div className="space-y-4 text-[13px]">
        <section><h4 className="mb-1 font-semibold">Finding</h4><p className="text-text-2">{f.finding}</p></section>
        <section><h4 className="mb-1.5 font-semibold">Evidence</h4><Evidence evidence={f.evidence} /></section>
        <section><h4 className="mb-1 font-semibold">Recommendation</h4><p className="text-text-2">{f.recommendation}</p></section>
        <div className="grid gap-4 sm:grid-cols-3">
          {f.expected_impact && <section><h4 className="mb-1 text-[12px] font-medium text-muted">Expected impact</h4><p>{f.expected_impact}</p></section>}
          <section><h4 className="mb-1 text-[12px] font-medium text-muted">Risk</h4><p>{f.risk}</p></section>
          <section><h4 className="mb-1 text-[12px] font-medium text-muted">Confidence</h4><p className="tabular">{Math.round(f.confidence * 100)}%</p></section>
        </div>
        {(f.guidance || f.remediation) && <section><h4 className="mb-1.5 font-semibold">Implementation guidance</h4><CodeBlock code={(f.remediation ?? f.guidance)!} /></section>}
        {f.validation && <ValidationDetail v={f.validation} />}
        <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-muted">
          DBManager never applies recommendations automatically. Make the change through your change process, or on a server in administration mode through its Administration tab (statements previewed, confirmed and audited) or the SQL workspace (classified, confirmed and audited).
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
          <span>First seen {dateTime(f.first_seen_at)}</span><span>Last evaluated {relTime(f.last_seen_at)}</span>
          <span>Status {f.status}{f.status_changed_by ? ` by ${f.status_changed_by}` : ""}{f.status_reason ? `: ${f.status_reason}` : ""}</span>
        </div>
        {can(perm) && <Field label="Note for the audit log (optional)"><Textarea rows={2} className="font-sans" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
      </div>
    </Dialog>
  );
}

export function FindingsList({ kind, serverId, group, defaultStatus = "open" }: { kind: Kind; serverId?: string; group?: string; defaultStatus?: string }) {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState(params.get("severity") ?? "");
  const category = params.get("category") ?? undefined;
  const [status, setStatus] = useState(defaultStatus);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Finding | null>(null);
  const path = kind === "security" ? "/security/findings" : "/recommendations";
  const fleetSource = useSourceParam();
  const source = serverId ? undefined : fleetSource;
  const data = useQuery({
    queryKey: [kind, serverId, group, source, q, severity, status, page, category],
    queryFn: () => get<Page<Finding>>(path, { server_id: serverId, source, group, q, category, severity: severity || undefined, status: status === "all" ? ["open", "acknowledged", "dismissed", "resolved"] : status === "open" ? ["open", "acknowledged"] : status, page, page_size: 50 }),
    placeholderData: (p) => p,
  });
  const focus = params.get("id");
  useEffect(() => {
    if (focus && data.data) {
      const f = data.data.items.find((x) => x.id === focus);
      if (f) setSelected(f);
    }
  }, [focus, data.data]);
  const columns: ColumnDef<Finding, unknown>[] = [
    { header: "Severity", accessorKey: "severity", cell: ({ getValue }) => <SeverityBadge severity={getValue() as string} /> },
    { header: "Finding", accessorKey: "title", cell: ({ row: { original: f } }) => (
      <div className="min-w-[280px]"><div className="font-medium">{f.title}</div><div className="max-w-[560px] truncate text-[12px] text-muted">{f.finding}</div></div>
    ) },
    ...(!serverId ? [{ header: "Server", accessorKey: "server_name", cell: ({ row: { original: f } }: { row: { original: Finding } }) => <span className="flex items-center gap-2 whitespace-nowrap"><DatabaseEngineIcon engine={f.engine} />{f.server_name}{f.is_demo && <SimulatedBadge />}</span> } as ColumnDef<Finding, unknown>] : []),
    { header: "Category", accessorKey: "category", cell: ({ getValue }) => <span className="capitalize text-text-2">{getValue() as string}</span> },
    { header: "Rule", accessorKey: "rule_id", cell: ({ getValue }) => <span className="font-mono text-[12px] text-muted">{getValue() as string}</span> },
    { header: "Confidence", accessorKey: "confidence", meta: { align: "right" }, cell: ({ getValue }) => `${Math.round((getValue() as number) * 100)}%` },
    { header: "Status", accessorKey: "status", cell: ({ row: { original: f } }) => (
      <span className="flex items-center gap-1"><Badge tone={f.status === "open" ? "accent" : f.status === "resolved" ? "ok" : "neutral"}>{f.status}</Badge>
        {f.status === "resolved" && f.validation && f.validation.status !== "not_measurable" && <ValidationBadge v={f.validation} />}</span>
    ) },
  ];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-muted" />
          <Input aria-label="Search" className="pl-8" placeholder="Title, rule or server" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <Select aria-label="Severity" className="w-36" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }}>
          <option value="">Any severity</option>{["critical", "high", "medium", "low", "info"].map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
        </Select>
        <Select aria-label="Status" className="w-40" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="open">Open and acknowledged</option><option value="dismissed">Dismissed</option><option value="resolved">Resolved</option><option value="all">All</option>
        </Select>
        <span className="ml-auto text-[12.5px] text-muted tabular">{data.data?.total ?? 0} {kind === "security" ? "findings" : "recommendations"}</span>
      </div>
      <DataTable columns={columns} data={data.data?.items} loading={data.isLoading} error={data.error} onRowClick={setSelected}
        manual={data.data ? { page, pageSize: 50, total: data.data.total, onPage: setPage } : undefined}
        empty={<EmptyState title={kind === "security" ? "No security findings" : "No recommendations"}>Rules run on a schedule against collected evidence. Nothing here means no rule found a problem.</EmptyState>} />
      {selected && <FindingDialog f={selected} kind={kind} onClose={() => { setSelected(null); if (focus) { params.delete("id"); setParams(params, { replace: true }); } }} />}
    </Card>
  );
}

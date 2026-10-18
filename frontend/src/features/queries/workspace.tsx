import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, History, Play, Plus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError, download, get, post } from "@/api/client";
import type { Classification, DatabaseInfo, ExecResult, Page } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { RiskBadge } from "@/components/status";
import { SqlEditor } from "@/components/sql-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { relTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ServerScopedPage } from "../shared/server-scoped";

interface Tab { id: number; title: string; sql: string; database: string }
interface HistoryItem { id: string; server_name: string; database: string | null; statement: string; statement_type: string; risk_class: string; status: string; duration_ms: number | null; row_count: number | null; created_at: string }

function loadTabs(): Tab[] {
  try {
    const raw = sessionStorage.getItem("dbm.ws.tabs");
    if (raw) return JSON.parse(raw);
  } catch { /* storage unavailable */ }
  return [{ id: 1, title: "Query 1", sql: "SELECT now();", database: "" }];
}

function ResultGrid({ r }: { r: ExecResult }) {
  if (!r.columns.length) return <div className="p-4 text-[13px]"><span className="font-medium">{r.status_message}</span>{r.committed && <Badge tone="ok" className="ml-2">committed</Badge>}</div>;
  return (
    <div className="scroll-thin max-h-[420px] overflow-auto">
      <table className="w-full border-collapse font-mono text-[12px]">
        <thead className="sticky top-0 bg-surface-2"><tr>
          <th className="border-b border-r border-border px-2 py-1.5 text-right font-normal text-muted">#</th>
          {r.columns.map((c) => <th key={c.name} className="whitespace-nowrap border-b border-r border-border px-2 py-1.5 text-left font-medium">{c.name} <span className="font-normal text-muted">{c.type}</span></th>)}
        </tr></thead>
        <tbody>{r.rows.map((row, i) => (
          <tr key={i} className="hover:bg-surface-2">
            <td className="border-b border-r border-border/60 px-2 py-1 text-right text-muted">{i + 1}</td>
            {row.map((v, j) => <td key={j} className="max-w-[420px] truncate whitespace-nowrap border-b border-r border-border/60 px-2 py-1">{v === null ? <span className="text-muted">NULL</span> : typeof v === "object" ? JSON.stringify(v) : String(v)}</td>)}
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function WorkspacePage() {
  return (
    <ServerScopedPage title="SQL workspace" description="Statements are classified before they run. Read-only roles can only run read-only statements; changes need confirmation and are audited."
      crumbs={[{ label: "Queries" }, { label: "SQL workspace" }]} perm="query.execute" cap="sql_workspace">
      {(s) => <Workspace key={s.id} serverId={s.id} serverName={s.name} engine={s.engine} defaultDb={s.default_database ?? ""} simulated={s.is_demo} />}
    </ServerScopedPage>
  );
}

function Workspace({ serverId, serverName, engine, defaultDb, simulated }: { serverId: string; serverName: string; engine: string; defaultDb: string; simulated: boolean }) {
  const qc = useQueryClient();
  const [tabs, setTabs] = useState<Tab[]>(loadTabs);
  const [active, setActive] = useState(tabs[0].id);
  const [results, setResults] = useState<Record<number, ExecResult | ApiError | undefined>>({});
  const [cls, setCls] = useState<Classification | null>(null);
  const [confirm, setConfirm] = useState<{ expected: string; c: Classification; production: boolean } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const tab = tabs.find((t) => t.id === active) ?? tabs[0];
  const dbs = useQuery({ queryKey: ["dbs", serverId], queryFn: () => get<DatabaseInfo[]>(`/servers/${serverId}/databases`) });
  const history = useQuery({ queryKey: ["ws-history", serverId], queryFn: () => get<Page<HistoryItem>>("/workspace/history", { server_id: serverId, page_size: 30 }), enabled: showHistory });

  useEffect(() => { try { sessionStorage.setItem("dbm.ws.tabs", JSON.stringify(tabs)); } catch { /* ignore */ } }, [tabs]);
  const update = (patch: Partial<Tab>) => setTabs((ts) => ts.map((t) => (t.id === tab.id ? { ...t, ...patch } : t)));

  useEffect(() => {
    const h = setTimeout(async () => {
      if (!tab.sql.trim()) return setCls(null);
      try { setCls(await post<Classification>("/workspace/classify", { sql: tab.sql, server_id: serverId, database: tab.database || null })); } catch { setCls(null); }
    }, 350);
    return () => clearTimeout(h);
  }, [tab.sql, tab.database, serverId]);

  const exec = useMutation({
    mutationFn: (v: { confirm?: string; reason?: string }) => post<ExecResult>("/workspace/execute", { server_id: serverId, database: tab.database || defaultDb || null, sql: tab.sql, confirm: v.confirm, reason: v.reason }),
    onSuccess: (r) => { setResults((p) => ({ ...p, [tab.id]: r })); setConfirm(null); qc.invalidateQueries({ queryKey: ["ws-history", serverId] }); if (!r.classification.read_only) toast.success(r.status_message ?? "Statement completed"); },
    onError: (e: ApiError) => {
      if (e.code === "confirmation_required") {
        const exp = e.details.expected === "server name" ? serverName : String(e.details.expected);
        setConfirm({ expected: exp, c: e.details.classification as Classification, production: Boolean(e.details.production) });
      } else setResults((p) => ({ ...p, [tab.id]: e }));
    },
  });
  const run = useCallback(() => { if (tab.sql.trim()) exec.mutate({}); }, [tab.sql, exec]);
  const res = results[tab.id];
  const blocked = (cls?.missing_permissions?.length ?? 0) > 0;

  return (
    <div className={cn("grid gap-4", showHistory && "xl:grid-cols-[1fr_auto]")}>
      <div className="min-w-0 space-y-3">
        <Card>
          <div className="flex items-center gap-1 overflow-x-auto border-b border-border px-2 pt-2">
            {tabs.map((t) => (
              <div key={t.id} className={cn("group flex items-center gap-1 rounded-t-md border border-b-0 px-3 py-1.5 text-[12.5px]", t.id === tab.id ? "border-border bg-surface font-medium" : "border-transparent text-muted hover:text-text")}>
                <button onClick={() => setActive(t.id)}>{t.title}</button>
                {tabs.length > 1 && <button aria-label={`Close ${t.title}`} className="opacity-50 hover:opacity-100" onClick={() => { const rest = tabs.filter((x) => x.id !== t.id); setTabs(rest); if (t.id === tab.id) setActive(rest[0].id); }}><X className="size-3" /></button>}
              </div>
            ))}
            <button aria-label="New tab" className="ml-1 rounded p-1 text-muted hover:bg-surface-3" onClick={() => { const id = Math.max(...tabs.map((t) => t.id)) + 1; setTabs([...tabs, { id, title: `Query ${id}`, sql: "", database: tab.database }]); setActive(id); }}><Plus className="size-4" /></button>
          </div>
          <div className="space-y-2 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select aria-label="Database" className="w-56" value={tab.database || defaultDb} onChange={(e) => update({ database: e.target.value })}>
                {(dbs.data ?? []).filter((d) => d.accessible && !d.is_template).map((d) => <option key={d.name}>{d.name}</option>)}
              </Select>
              {cls && <><RiskBadge risk={cls.risk} /><span className="text-[12px] text-muted">{cls.statement_type}{cls.objects.length ? ` on ${cls.objects.slice(0, 3).join(", ")}` : ""}</span></>}
              {blocked && <Tooltip content={`Missing: ${cls!.missing_permissions!.join(", ")}`}><span><Badge tone="crit">not permitted for your role</Badge></span></Tooltip>}
              {simulated && <Badge tone="outline">simulated server: nothing is executed</Badge>}
              <div className="ml-auto flex items-center gap-2">
                <Button size="sm" variant="ghost" onClick={() => setShowHistory((v) => !v)}><History /> History</Button>
                <Button size="sm" disabled={!cls?.read_only} onClick={() => download("/workspace/export.csv", "query-result.csv", { method: "POST", body: { server_id: serverId, database: tab.database || defaultDb || null, sql: tab.sql } }).catch((e) => toast.error(e.message))}><Download /> CSV</Button>
                <Button size="sm" variant="primary" onClick={run} disabled={exec.isPending || !tab.sql.trim() || blocked}><Play /> {exec.isPending ? "Running…" : "Run"} <kbd className="ml-1 rounded bg-white/15 px-1 font-mono text-2xs">Ctrl ↵</kbd></Button>
              </div>
            </div>
            <SqlEditor value={tab.sql} onChange={(v) => update({ sql: v })} engine={engine} onRun={run} height="240px" />
            {cls && cls.reasons.length > 0 && cls.risk !== "READ" && <p className="text-[12px] text-muted">{cls.reasons.join("; ")}</p>}
          </div>
        </Card>
        <Card>
          {res instanceof ApiError ? <ErrorState error={res} /> : res ? (
            <>
              <div className="flex flex-wrap items-center gap-3 border-b border-border px-3 py-2 text-[12.5px] text-muted">
                <span className="font-medium text-text">{res.status_message}</span>
                <span>{res.duration_ms} ms</span>
                {res.truncated && <Badge tone="warn">first {res.max_rows} rows shown</Badge>}
                {res.simulated && <Badge tone="outline">simulated</Badge>}
              </div>
              <ResultGrid r={res} />
            </>
          ) : <EmptyState title="Results appear here">Run a statement with the Run button or Ctrl+Enter. Results are limited to 500 rows by default and statements time out after 30 seconds.</EmptyState>}
        </Card>
      </div>
      {showHistory && (
        <Card className="w-full xl:w-[340px]">
          <div className="border-b border-border px-3 py-2 text-[13px] font-semibold">Your history on this server</div>
          <ul className="scroll-thin max-h-[640px] divide-y divide-border overflow-y-auto">
            {history.data?.items.map((h) => (
              <li key={h.id}>
                <button className="w-full px-3 py-2 text-left hover:bg-surface-2" onClick={() => update({ sql: h.statement })}>
                  <code className="line-clamp-2 font-mono text-[11.5px]">{h.statement}</code>
                  <div className="mt-1 flex items-center gap-2 text-2xs text-muted"><RiskBadge risk={h.risk_class} /><span>{h.status}</span><span>{relTime(h.created_at)}</span></div>
                </button>
              </li>
            ))}
            {history.data?.items.length === 0 && <li className="p-4 text-[13px] text-muted">No statements yet</li>}
          </ul>
        </Card>
      )}
      {confirm && (
        <ConfirmDialog open onOpenChange={(o) => !o && setConfirm(null)} title={confirm.c.risk === "DESTRUCTIVE" ? "Run a destructive statement?" : "Run this change?"}
          danger={confirm.c.risk === "DESTRUCTIVE" || confirm.production} expected={confirm.expected}
          expectedLabel={confirm.expected === serverName ? `Type ${serverName} to confirm` : `Type ${confirm.expected} to confirm`}
          askReason reasonRequired={confirm.production && confirm.c.risk === "DESTRUCTIVE"} confirmLabel="Run statement" busy={exec.isPending}
          onConfirm={(typed, reason) => exec.mutate({ confirm: typed, reason })}>
          <div className="space-y-2 text-[13px]">
            <div className="flex items-center gap-2"><RiskBadge risk={confirm.c.risk} /><span className="font-medium">{confirm.c.statement_type}</span>{confirm.production && <Badge tone="crit">production</Badge>}</div>
            <ul className="list-disc pl-5 text-text-2">{confirm.c.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
            <code className="block max-h-32 overflow-auto rounded bg-surface-2 p-2 font-mono text-[12px]">{tab.sql}</code>
          </div>
        </ConfirmDialog>
      )}
    </div>
  );
}

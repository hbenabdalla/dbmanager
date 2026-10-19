import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileSearch, Gauge } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, get, post } from "@/api/client";
import { serverCan } from "@/api/hooks";
import type { DatabaseInfo, ExplainResult, ServerDetail } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { PageHeader } from "@/components/page";
import { PlanViewer } from "@/components/plan-graph";
import { SqlEditor } from "@/components/sql-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { compactNum, ms } from "@/lib/format";
import { QueryStatsTable } from "../shared/query-stats";
import { ServerScopedPage } from "../shared/server-scoped";

export function QueryPerformancePage() {
  return (
    <div>
      <PageHeader title="Query performance" description="Most expensive statements across the servers you can see, from each engine's own statistics (pg_stat_statements, DMVs, V$SQLSTATS, package cache)."
        crumbs={[{ label: "Queries" }, { label: "Query performance" }]} />
      <QueryStatsTable fleet />
    </div>
  );
}

export function SlowQueriesPage() {
  return (
    <div>
      <PageHeader title="Slow queries" description="Statements averaging more than 500 ms per execution." crumbs={[{ label: "Queries" }, { label: "Slow queries" }]} />
      <QueryStatsTable fleet slowOnly />
    </div>
  );
}

const SEV_TONE: Record<string, "crit" | "warn" | "info"> = { high: "crit", medium: "warn", low: "info" };

function ExplainWorkbench({ server, initialSql, initialDb, fingerprint }: { server: ServerDetail; initialSql: string; initialDb?: string; fingerprint?: string }) {
  const [sql, setSql] = useState(initialSql);
  const [saveLabel, setSaveLabel] = useState("");
  const dbs = useQuery({ queryKey: ["dbs", server.id], queryFn: () => get<DatabaseInfo[]>(`/servers/${server.id}/databases`) });
  const [database, setDatabase] = useState(initialDb || server.default_database || "");
  const [confirm, setConfirm] = useState<{ expected: string; reasons: string[]; production: boolean } | null>(null);
  const [result, setResult] = useState<(ExplainResult & { snapshot_id?: string }) | null>(null);
  const run = useMutation({
    mutationFn: (v: { analyze: boolean; confirm?: string; reason?: string }) =>
      post<ExplainResult & { snapshot_id?: string }>(`/servers/${server.id}/explain`, {
        sql, database: database || null, analyze: v.analyze, confirm: v.confirm, reason: v.reason,
        save_label: saveLabel.trim() || undefined, fingerprint: saveLabel.trim() && fingerprint ? fingerprint : undefined,
      }),
    onSuccess: (r) => {
      setResult(r); setConfirm(null);
      if (r.snapshot_id) { toast.success(`Plan saved as “${saveLabel.trim()}”`); setSaveLabel(""); }
    },
    onError: (e: ApiError) => {
      if (e.code === "confirmation_required") {
        const exp = e.details.expected === "server name" ? server.name : String(e.details.expected);
        setConfirm({ expected: exp, reasons: (e.details.reasons as string[]) ?? [], production: Boolean(e.details.production) });
      }
    },
  });
  const canAnalyze = serverCan(server, "query.explain_analyze");
  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Database" className="w-56" value={database} onChange={(e) => setDatabase(e.target.value)}>
              <option value="">Default database</option>
              {(dbs.data ?? []).filter((d) => d.accessible && !d.is_template).map((d) => <option key={d.name}>{d.name}</option>)}
            </Select>
            <span className="text-[12px] text-muted">Paste a statement without the EXPLAIN keyword. Parameterized statements ($1) produce a generic plan on PostgreSQL 16+.</span>
          </div>
          <SqlEditor value={sql} onChange={setSql} engine={server.engine} height="180px" onRun={() => run.mutate({ analyze: false })} />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" onClick={() => run.mutate({ analyze: false })} disabled={!sql.trim() || run.isPending}><FileSearch /> Explain (estimate)</Button>
            {canAnalyze && <Button onClick={() => run.mutate({ analyze: true })} disabled={!sql.trim() || run.isPending}><Gauge /> Explain analyze (executes)</Button>}
            <Input aria-label="Save plan as" className="w-52" placeholder="Save plan as (optional)" maxLength={128} value={saveLabel} onChange={(e) => setSaveLabel(e.target.value)} />
            <Link className="text-[12.5px] text-accent hover:underline" to={`/queries/plans/compare?server=${server.id}`}>Compare saved plans</Link>
            <span className="text-[12px] text-muted">Estimate never runs the statement. Analyze runs it inside a transaction that is always rolled back.</span>
          </div>
        </CardBody>
      </Card>
      {run.error && (run.error as ApiError).code !== "confirmation_required" && <Card><ErrorState error={run.error} /></Card>}
      {result?.root ? (
        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader title="Plan" description={<span className="flex flex-wrap gap-x-3">
              <span>{result.mode === "analyze" ? "Actual execution" : "Estimated"}</span><span>cost {compactNum(result.total_cost)}</span>
              {result.planning_ms != null && <span>planning {ms(result.planning_ms)}</span>}{result.execution_ms != null && <span>execution {ms(result.execution_ms)}</span>}
            </span>} actions={result.mode === "analyze" ? <Badge tone="warn">executed, rolled back</Badge> : <Badge tone="ok">not executed</Badge>} />
            <CardBody><PlanViewer root={result.root} issues={result.issues} analyzed={result.mode === "analyze"} raw={result.raw} engine={result.engine} /></CardBody>
          </Card>
          <Card>
            <CardHeader title="Detected issues" description={`${result.issues.length} found`} />
            <CardBody className="space-y-3">
              {result.issues.length === 0 && <p className="text-[13px] text-muted">No problems detected by the plan rules.</p>}
              {result.issues.map((i, n) => (
                <div key={n} className="text-[13px]">
                  <div className="mb-0.5 flex items-center gap-2"><Badge tone={SEV_TONE[i.severity] ?? "info"}>{i.severity}</Badge><span className="font-medium">{i.title}</span></div>
                  <p className="text-text-2">{i.detail}</p>
                </div>
              ))}
              {result.notes.map((n) => <p key={n} className="border-t border-border pt-2 text-[12px] text-muted">{n}</p>)}
            </CardBody>
          </Card>
        </div>
      ) : !run.isPending && !result && <Card><EmptyState icon={<FileSearch />} title="Run EXPLAIN to see the plan">Operators are shaded by their share of the total cost. Full scans, spills, expensive joins and bad estimates are flagged.</EmptyState></Card>}
      {confirm && (
        <ConfirmDialog open onOpenChange={(o) => !o && setConfirm(null)} title="Run EXPLAIN ANALYZE?" danger={confirm.production}
          expected={confirm.expected} expectedLabel={confirm.expected === server.name ? `Type ${server.name} to confirm` : "Type ANALYZE to confirm"}
          confirmLabel="Run and roll back" askReason busy={run.isPending}
          onConfirm={(typed, reason) => run.mutate({ analyze: true, confirm: typed, reason })}>
          <div className="flex gap-2 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[13px] text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>The statement is executed for real{confirm.production ? " on a production server" : ""}. Data changes are rolled back, but load, locks and side effects such as sequence increments still happen.</div>
          </div>
        </ConfirmDialog>
      )}
    </div>
  );
}

export function ExecutionPlansPage() {
  const [params] = useSearchParams();
  return (
    <ServerScopedPage title="Execution plans" description="Readable plan trees with issue detection. EXPLAIN and EXPLAIN ANALYZE are kept separate on purpose."
      crumbs={[{ label: "Queries" }, { label: "Execution plans" }]} perm="query.explain" cap="explain">
      {(s) => <ExplainWorkbench key={s.id} server={s} fingerprint={params.get("fp") ?? undefined} initialSql={params.get("sql") ?? "SELECT *\nFROM orders o\nJOIN customers c ON c.id = o.customer_id\nWHERE o.status = 'open'\nORDER BY o.created_at DESC"} initialDb={params.get("database") ?? undefined} />}
    </ServerScopedPage>
  );
}

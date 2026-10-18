import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Ban, Lock, OctagonX, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError, get, post } from "@/api/client";
import { serverCan } from "@/api/hooks";
import type { LockInfo, ServerDetail, SessionInfo } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Input, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { duration } from "@/lib/format";

function Sql({ text }: { text: string | null }) {
  if (!text) return <span className="text-muted">—</span>;
  return <Tooltip content={<span className="whitespace-pre-wrap font-mono text-[11px]">{text.slice(0, 1200)}</span>}><code className="block max-w-[320px] truncate font-mono text-[12px] text-text-2">{text}</code></Tooltip>;
}

const STATE_TONE: Record<string, "ok" | "warn" | "neutral" | "crit"> = { active: "ok", idle: "neutral", "idle in transaction": "warn", "idle in transaction (aborted)": "crit" };

export function SessionsTab({ server }: { server: ServerDetail }) {
  const qc = useQueryClient();
  const [state, setState] = useState("");
  const [q, setQ] = useState("");
  const [target, setTarget] = useState<{ s: SessionInfo; terminate: boolean } | null>(null);
  const sessions = useQuery({ queryKey: ["sessions", server.id], queryFn: () => get<SessionInfo[]>(`/servers/${server.id}/sessions`), refetchInterval: 10_000 });
  const manage = serverCan(server, "session.manage");
  const cancel = useMutation({
    mutationFn: ({ s, terminate, typed, reason }: { s: SessionInfo; terminate: boolean; typed: string; reason: string }) =>
      post<{ ok: boolean }>(`/servers/${server.id}/sessions/${s.session_id}/cancel`, { terminate, confirm: typed || undefined, reason: reason || undefined }),
    onSuccess: (r, v) => {
      toast[r.ok ? "success" : "warning"](r.ok ? (v.terminate ? "Session terminated" : "Query cancelled") : "The session had already finished");
      setTarget(null);
      qc.invalidateQueries({ queryKey: ["sessions", server.id] });
    },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const rows = useMemo(() => (sessions.data ?? []).filter((s) =>
    (!state || s.state === state) && (!q || [s.username, s.database, s.application, s.query, s.client_address].some((x) => x?.toLowerCase().includes(q.toLowerCase())))), [sessions.data, state, q]);
  const blocked = (sessions.data ?? []).filter((s) => s.blocked_by.length).length;
  const columns: ColumnDef<SessionInfo, unknown>[] = [
    { header: "Session", accessorKey: "session_id", cell: ({ row: { original: s } }) => <span className="font-mono text-[12px]">{s.session_id}</span> },
    { header: "User", accessorKey: "username" },
    { header: "Database", accessorKey: "database" },
    { header: "Application", accessorKey: "application", cell: ({ getValue }) => <span className="text-text-2">{(getValue() as string) ?? "—"}</span> },
    { header: "State", accessorKey: "state", cell: ({ row: { original: s } }) => (
      <span className="flex items-center gap-1.5">
        {s.state ? <Badge tone={STATE_TONE[s.state] ?? "neutral"}>{s.state}</Badge> : <span className="text-muted">{s.backend_type}</span>}
        {s.blocked_by.length > 0 && <Tooltip content={`Waiting on session ${s.blocked_by.join(", ")}`}><span><Badge tone="crit"><Lock className="size-3" /> blocked</Badge></span></Tooltip>}
      </span>
    ) },
    { header: "Wait", accessorFn: (s) => s.wait_event ? `${s.wait_event_type}: ${s.wait_event}` : "", cell: ({ getValue }) => <span className="text-[12px] text-muted">{(getValue() as string) || "—"}</span> },
    { header: "Duration", accessorKey: "duration_seconds", meta: { align: "right" }, cell: ({ getValue }) => duration(getValue() as number) },
    { header: "Query", accessorKey: "query", enableSorting: false, cell: ({ getValue }) => <Sql text={getValue() as string | null} /> },
    ...(manage ? [{
      id: "actions", header: "", enableSorting: false, cell: ({ row: { original: s } }: { row: { original: SessionInfo } }) => (s.backend_type ?? "client backend") === "client backend" ? (
        <div className="flex justify-end gap-1">
          <Tooltip content="Cancel the running statement"><Button size="icon" variant="ghost" aria-label="Cancel query" onClick={() => setTarget({ s, terminate: false })}><Ban /></Button></Tooltip>
          <Tooltip content="Terminate the session"><Button size="icon" variant="ghost" aria-label="Terminate session" onClick={() => setTarget({ s, terminate: true })}><OctagonX className="text-crit" /></Button></Tooltip>
        </div>
      ) : null,
    } as ColumnDef<SessionInfo, unknown>] : []),
  ];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <Input aria-label="Filter sessions" className="w-64" placeholder="User, database, app, SQL" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select aria-label="State" className="w-48" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">Any state</option>{["active", "idle", "idle in transaction", "idle in transaction (aborted)"].map((s) => <option key={s}>{s}</option>)}
        </Select>
        <span className="ml-auto text-[12.5px] text-muted tabular">{sessions.data?.length ?? 0} sessions{blocked ? `, ${blocked} blocked` : ""}</span>
        <Button size="sm" variant="ghost" onClick={() => sessions.refetch()} aria-label="Refresh"><RefreshCw className={sessions.isFetching ? "animate-spin" : ""} /></Button>
      </div>
      <DataTable columns={columns} data={rows} loading={sessions.isLoading} error={sessions.error} onRetry={() => sessions.refetch()} dense
        initialSort={[{ id: "duration_seconds", desc: true }]} rowClassName={(s) => (s.blocked_by.length ? "bg-crit-soft/30" : undefined)}
        empty={<EmptyState title="No sessions match" />} />
      {target && (
        <ConfirmDialog open onOpenChange={(o) => !o && setTarget(null)} busy={cancel.isPending}
          title={target.terminate ? `Terminate session ${target.s.session_id}?` : `Cancel the query in session ${target.s.session_id}?`}
          danger={target.terminate} expected={target.terminate ? target.s.session_id : undefined} expectedLabel="Type the session id to confirm"
          confirmLabel={target.terminate ? "Terminate session" : "Cancel query"} askReason
          onConfirm={(typed, reason) => cancel.mutate({ s: target.s, terminate: target.terminate, typed, reason })}>
          <p className="text-[13px] text-text-2">
            {target.terminate ? "The client connection is closed and any open transaction is rolled back." : "The running statement is cancelled; the session stays connected."}
            {" "}User <span className="font-medium">{target.s.username}</span> on <span className="font-medium">{target.s.database}</span>.
          </p>
          {target.s.query && <code className="mt-2 block max-h-32 overflow-auto rounded bg-surface-2 p-2 font-mono text-[12px]">{target.s.query}</code>}
        </ConfirmDialog>
      )}
    </Card>
  );
}

export function LocksTab({ server }: { server: ServerDetail }) {
  const locks = useQuery({ queryKey: ["locks", server.id], queryFn: () => get<LockInfo[]>(`/servers/${server.id}/locks`), refetchInterval: 10_000 });
  const [onlyWaiting, setOnlyWaiting] = useState(false);
  const rows = (locks.data ?? []).filter((l) => !onlyWaiting || !l.granted);
  const waiting = (locks.data ?? []).filter((l) => !l.granted);
  const blockers = [...new Set(waiting.flatMap((l) => l.blocked_by))];
  const columns: ColumnDef<LockInfo, unknown>[] = [
    { header: "Session", accessorKey: "session_id", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
    { header: "Granted", accessorKey: "granted", cell: ({ getValue }) => getValue() ? <Badge tone="ok">granted</Badge> : <Badge tone="crit">waiting</Badge> },
    { header: "Type", accessorKey: "lock_type" },
    { header: "Mode", accessorKey: "mode", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
    { header: "Relation", accessorKey: "relation" },
    { header: "User", accessorKey: "username" },
    { header: "Blocked by", accessorFn: (l) => l.blocked_by.join(", "), cell: ({ getValue }) => <span className="font-mono text-[12px] text-crit">{getValue() as string}</span> },
    { header: "Waiting", accessorKey: "waiting_seconds", meta: { align: "right" }, cell: ({ getValue }) => duration(getValue() as number) },
    { header: "Query", accessorKey: "query", enableSorting: false, cell: ({ getValue }) => <Sql text={getValue() as string | null} /> },
  ];
  return (
    <div className="space-y-4">
      {blockers.length > 0 && (
        <Card className="border-crit/30 bg-crit-soft/40 p-4 text-[13px]">
          <span className="font-medium text-crit">{waiting.length} lock request{waiting.length === 1 ? "" : "s"} waiting.</span>{" "}
          Root blocking session{blockers.length === 1 ? "" : "s"}: <span className="font-mono">{blockers.join(", ")}</span>. Open the Sessions tab to inspect or cancel them.
        </Card>
      )}
      <Card>
        <div className="flex items-center gap-3 border-b border-border p-3 text-[13px]">
          <label className="flex items-center gap-2"><input type="checkbox" checked={onlyWaiting} onChange={(e) => setOnlyWaiting(e.target.checked)} /> Only waiting locks</label>
          <span className="ml-auto text-[12.5px] text-muted tabular">{locks.data?.length ?? 0} locks</span>
        </div>
        <DataTable columns={columns} data={rows} loading={locks.isLoading} error={locks.error} dense initialSort={[{ id: "granted", desc: false }]}
          empty={<EmptyState title={onlyWaiting ? "No waiting locks" : "No locks held"} />} />
      </Card>
    </div>
  );
}

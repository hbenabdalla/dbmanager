import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseZap } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError, get, put } from "@/api/client";
import { KeyValue, PageHeader, Stat } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { compactNum, dateTime, relTime } from "@/lib/format";

interface Ingestion {
  running: boolean; pending: number; buffer_max: number; batch_size: number; flush_interval_seconds: number;
  written_total: number; failed_batches: number; dropped_total: number; retry_backoff_seconds: number;
  last_success: string | null; last_failure: string | null; last_error: string | null; state: string;
}
interface StorageStatus {
  enabled: boolean; status: "available" | "unavailable"; engine: string; version: string | null; endpoint: string | null;
  error: string | null; series: number | null; samples: number | null; oldest_metric: string | null; newest_metric: string | null;
  retention: { desired_days: number; effective: string | null; effective_days: number | null; in_sync: boolean; how_to_apply: string | null };
  details: Record<string, unknown>; ingestion: Ingestion; pending_deletes: number; checked_at: string;
}

const INGEST_TONE: Record<string, "ok" | "warn" | "crit" | "neutral"> = { ok: "ok", idle: "neutral", retrying: "crit", backlogged: "warn" };
const INGEST_LABEL: Record<string, string> = {
  ok: "Writing normally", idle: "Not running in this process", retrying: "Retrying failed writes", backlogged: "Buffer filling up",
};

export function MonitoringStoragePage() {
  const qc = useQueryClient();
  const { canPlatform } = useAuth();
  const q = useQuery({ queryKey: ["monitoring-storage"], queryFn: () => get<StorageStatus>("/platform/monitoring-storage"), refetchInterval: 30_000 });
  const [days, setDays] = useState<number | "">("");
  useEffect(() => { if (q.data) setDays(q.data.retention.desired_days); }, [q.data?.retention.desired_days]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => put<StorageStatus>("/platform/monitoring-storage/retention", { days: Number(days) }),
    onSuccess: (d) => { qc.setQueryData(["monitoring-storage"], d); toast.success("Retention policy saved"); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const d = q.data;
  const canEdit = canPlatform("settings.manage");
  return (
    <div>
      <PageHeader title="Monitoring storage"
        description="Metric history lives in a dedicated time-series database. PostgreSQL keeps inventory, configuration, findings, audit and jobs."
        crumbs={[{ label: "Platform" }, { label: "Monitoring storage" }]}
        meta={d && <span>Checked {relTime(d.checked_at)}</span>} />
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !d ? <SkeletonRows /> : (
        <>
          {d.status === "unavailable" && (
            <Card className="mb-4 border-crit/40">
              <CardBody className="flex items-start gap-3">
                <DatabaseZap className="mt-0.5 h-5 w-5 shrink-0 text-crit" aria-hidden />
                <div className="text-[13px]">
                  <div className="font-semibold">Monitoring Storage Unavailable</div>
                  <p className="text-muted">Charts and history can't be read and new samples are buffered in memory ({compactNum(d.ingestion.pending)} waiting). Check the <span className="font-mono">database-manager-timeseries</span> container.</p>
                  {d.error && <p className="mt-1 font-mono text-[12px] text-crit">{d.error}</p>}
                </div>
              </CardBody>
            </Card>
          )}
          <Card className="mb-4 grid grid-cols-2 divide-x divide-border sm:grid-cols-5">
            <Stat label="Status" value={<Badge tone={d.status === "available" ? "ok" : "crit"}>{d.status === "available" ? "Available" : "Unavailable"}</Badge>} sub={d.engine} />
            <Stat label="Series" value={d.series != null ? compactNum(d.series) : "–"} sub="distinct label sets" />
            <Stat label="Samples stored" value={d.samples != null ? compactNum(d.samples) : "–"} />
            <Stat label="Oldest metric" value={<span className="text-[15px]">{d.oldest_metric ? relTime(d.oldest_metric) : "–"}</span>} sub={d.oldest_metric ? dateTime(d.oldest_metric) : undefined} />
            <Stat label="Newest metric" value={<span className="text-[15px]">{d.newest_metric ? relTime(d.newest_metric) : "–"}</span>} sub={d.newest_metric ? dateTime(d.newest_metric) : undefined} />
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title="Ingestion" description="Batch writer of this backend process" />
              <CardBody>
                <div className="mb-3"><Badge tone={INGEST_TONE[d.ingestion.state] ?? "neutral"}>{INGEST_LABEL[d.ingestion.state] ?? d.ingestion.state}</Badge></div>
                <KeyValue items={[
                  ["Samples written", compactNum(d.ingestion.written_total)],
                  ["Waiting in buffer", `${compactNum(d.ingestion.pending)} of ${compactNum(d.ingestion.buffer_max)}`],
                  ["Batch size / flush interval", `${compactNum(d.ingestion.batch_size)} samples / ${d.ingestion.flush_interval_seconds}s`],
                  ["Last successful write", d.ingestion.last_success ? `${relTime(d.ingestion.last_success)} (${dateTime(d.ingestion.last_success)})` : "–"],
                  ["Last failed write", d.ingestion.last_failure ? `${relTime(d.ingestion.last_failure)} (${dateTime(d.ingestion.last_failure)})` : "none"],
                  ["Failed batches", String(d.ingestion.failed_batches)],
                  ["Samples dropped (buffer full)", String(d.ingestion.dropped_total)],
                  ...(d.ingestion.last_error ? [["Last error", <span key="e" className="font-mono text-[12px] text-crit">{d.ingestion.last_error}</span>] as [string, React.ReactNode]] : []),
                ]} />
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Engine and retention" />
              <CardBody>
                <KeyValue items={[
                  ["Engine", d.engine],
                  ["Version", d.version ?? "–"],
                  ["Endpoint (internal)", d.endpoint ? <span key="u" className="font-mono text-[12px]">{d.endpoint}</span> : "–"],
                  ["Deduplication", String(d.details.deduplication ?? "–")],
                  ["Retention in effect", d.retention.effective ?? "–"],
                  ["Retention policy", `${d.retention.desired_days} days`],
                  ["Pending deletions", d.pending_deletes ? `${d.pending_deletes} (retried by the retention job)` : "none"],
                ]} />
                {!d.retention.in_sync && (
                  <p className="mt-3 rounded-md bg-warn-soft px-3 py-2 text-[12.5px]">
                    The policy ({d.retention.desired_days} days) differs from what the storage enforces ({d.retention.effective}). {d.retention.how_to_apply}
                  </p>
                )}
                {canEdit && (
                  <form className="mt-4 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
                    <Field label="Retention policy (days)" hint="Applied by the time-series database: set DBM_TIMESERIES_RETENTION to match and restart it.">
                      <Input type="number" min={1} max={3650} className="w-32" value={days} onChange={(e) => setDays(e.target.value === "" ? "" : Number(e.target.value))} />
                    </Field>
                    <Button type="submit" variant="primary" size="sm" disabled={save.isPending || days === "" || days === d.retention.desired_days}>Save policy</Button>
                  </form>
                )}
              </CardBody>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

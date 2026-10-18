import { useQuery } from "@tanstack/react-query";
import { get } from "@/api/client";
import type { LicensableFeature } from "@/api/types";
import { PageHeader } from "@/components/page";
import { EngineMark } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tooltip } from "@/components/ui/tooltip";
import { matrixTone, SUPPORT_LABEL } from "@/lib/capabilities";

interface MatrixCell { status: string; level: string; note: string | null; engine_level: string; validated?: string | null; validation_scope?: string | null }
interface Validation {
  status: "validated" | "failed" | "pending" | "external"; version: string | null; validated_at: string | null;
  environment?: string | null; capabilities: Record<string, string>; note: string | null;
}
interface MatrixEngine {
  engine: string; display_name: string; implemented: boolean; support_level: string; support_note: string; driver: string;
  driver_status: string | null; driver_detail?: { error: string | null; remediation: string | null } | null;
  validation?: Validation | null; engine_specific_rules: number; generic_rules: number; capabilities: Record<string, MatrixCell>;
  licensable_features?: LicensableFeature[];
}
interface Matrix { rows: string[]; engines: MatrixEngine[] }

interface Driver {
  key: string; driver: string; engines: string[]; description: string; license: string; client_dependency: string | null;
  version: string | null; status: "installed" | "degraded" | "missing"; error: string | null; remediation?: string | null; details?: Record<string, unknown>;
}

const DRIVER_TONE: Record<string, "ok" | "warn" | "crit"> = { installed: "ok", degraded: "warn", missing: "crit" };
const DRIVER_LABEL: Record<string, string> = { installed: "Installed", degraded: "Degraded", missing: "MISSING" };
const VALIDATION: Record<string, { label: string; tone: "ok" | "warn" | "crit" | "neutral" }> = {
  validated: { label: "Validated", tone: "ok" },
  failed: { label: "Validation failed", tone: "crit" },
  pending: { label: "Validation pending", tone: "warn" },
  external: { label: "External validation", tone: "neutral" },
};

function ValidationBadge({ v }: { v: Validation }) {
  const meta = VALIDATION[v.status] ?? VALIDATION.pending;
  const tip = v.status === "validated" || v.status === "failed"
    ? `Real-engine suite against ${v.version ?? "?"}${v.validated_at ? ` on ${v.validated_at.slice(0, 10)}` : ""}`
    : v.note ?? meta.label;
  return <Tooltip content={tip}><span><Badge tone={meta.tone}>{meta.label}</Badge></span></Tooltip>;
}

export function EngineSupportPage() {
  const q = useQuery({ queryKey: ["engine-support"], queryFn: () => get<Matrix>("/platform/engine-support") });
  return (
    <div>
      <PageHeader title="Engine support" crumbs={[{ label: "Platform" }, { label: "Engine support" }]}
        description="Generated from the adapters' capability definitions. A server's own page shows what runtime discovery found on that server (privileges, configuration, licensing)." />
      {q.isLoading ? <SkeletonRows rows={14} /> : q.error || !q.data ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div className="space-y-4">
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[920px] border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-border text-left">
                    <th className="sticky left-0 bg-surface px-3 py-2 font-medium text-muted">Capability</th>
                    {q.data.engines.map((e) => (
                      <th key={e.engine} className="px-2 py-2 align-bottom font-medium">
                        <div className="flex flex-col gap-1">
                          <EngineMark engine={e.engine} withLabel />
                          <Tooltip content={e.support_note || e.display_name}>
                            <span><Badge tone={SUPPORT_LABEL[e.support_level]?.tone ?? "neutral"}>{SUPPORT_LABEL[e.support_level]?.label ?? e.support_level}</Badge></span>
                          </Tooltip>
                          {e.validation && <ValidationBadge v={e.validation} />}
                          {e.driver_status === "missing" && <Tooltip content={e.driver_detail?.remediation ?? e.driver_detail?.error ?? "driver missing"}><span><Badge tone="crit">Driver MISSING</Badge></span></Tooltip>}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {q.data.rows.map((row) => (
                    <tr key={row} className="border-b border-border last:border-0">
                      <td className="sticky left-0 bg-surface px-3 py-1.5 text-text-2">{row}</td>
                      {q.data!.engines.map((e) => {
                        const c = e.capabilities[row];
                        // FULL is only claimed outright when the real-engine suite proved it
                        const unproven = c && e.validation && c.status === "FULL" && c.validated !== "validated";
                        const label = c ? (unproven ? "FULL*" : c.status) : "";
                        const tip = c ? `${c.note ?? c.status}${unproven ? " — declared by the adapter; not yet validated against a real engine" : c.validated === "validated" ? ` — validated against a real engine${c.validation_scope ? ` (${c.validation_scope})` : ""}` : ""}` : "";
                        return (
                          <td key={e.engine} className="px-2 py-1.5">
                            {c ? <Tooltip content={tip}><span><Badge tone={unproven ? "info" : matrixTone(c.status)}>{label}</Badge></span></Tooltip> : <span className="text-muted">—</span>}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  <tr className="border-b border-border">
                    <td className="sticky left-0 bg-surface px-3 py-1.5 text-text-2">Validated against</td>
                    {q.data.engines.map((e) => <td key={e.engine} className="px-2 py-1.5 font-mono text-2xs text-muted">{e.validation?.version ?? (e.validation ? "—" : "n/a")}</td>)}
                  </tr>
                  <tr>
                    <td className="sticky left-0 bg-surface px-3 py-1.5 text-text-2">Engine-specific rules</td>
                    {q.data.engines.map((e) => <td key={e.engine} className="tabular px-2 py-1.5">{e.engine_specific_rules} <span className="text-muted">+ {e.generic_rules} common</span></td>)}
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="border-t border-border px-3 py-2 text-[12px] text-muted">
              FULL* = implemented by the adapter but not yet proven by the real-engine suite (scripts/integration-test). Hover a cell for details.
            </p>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            {q.data.engines.filter((e) => e.support_note || (e.licensable_features ?? []).length).map((e) => (
              <Card key={e.engine}>
                <CardHeader title={<EngineMark engine={e.engine} withLabel />} description={e.driver} />
                <CardBody className="space-y-2 text-[12.5px] text-text-2">
                  <p>{e.support_note}</p>
                  {(e.licensable_features ?? []).map((lf) => (
                    <div key={lf.key} className="rounded-md border border-border bg-surface-2 px-3 py-2">
                      <div className="flex items-center gap-2 font-medium">{lf.label} <Badge tone="neutral">Disabled by default</Badge></div>
                      <div className="text-muted">Covers {lf.covers}. {lf.used_by_db_manager}.</div>
                    </div>
                  ))}
                </CardBody>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function DatabaseDriversPage() {
  const q = useQuery({ queryKey: ["platform-drivers"], queryFn: () => get<Driver[]>("/platform/drivers") });
  return (
    <div>
      <PageHeader title="Database drivers" crumbs={[{ label: "Platform" }, { label: "Database drivers" }]}
        description="Python drivers and client libraries installed in this backend. A missing driver makes its engines report DRIVER_NOT_INSTALLED instead of failing silently." />
      {q.isLoading ? <SkeletonRows rows={6} /> : q.error || !q.data ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <div className="space-y-3">
        {q.data.filter((d) => d.status === "missing").map((d) => (
          <div key={d.key} role="alert" className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">
            <span className="font-semibold">{d.key === "pyodbc" ? "SQL Server Driver MISSING" : `${d.driver} driver MISSING`}</span>
            {" — "}{d.remediation ?? d.error} Connections to {d.engines.join(", ")} servers fail until it is installed.
          </div>
        ))}
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-[13px]">
              <thead>
                <tr className="border-b border-border text-left text-[12px] text-muted">
                  <th className="px-3 py-2 font-medium">Engines</th><th className="px-3 py-2 font-medium">Driver</th>
                  <th className="px-3 py-2 font-medium">Version</th><th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Client dependency</th>
                </tr>
              </thead>
              <tbody>
                {q.data.map((d) => (
                  <tr key={d.key} className="border-b border-border align-top last:border-0">
                    <td className="px-3 py-2"><div className="flex flex-col gap-1">{d.engines.map((e) => <EngineMark key={e} engine={e} withLabel />)}</div></td>
                    <td className="px-3 py-2"><div className="font-medium">{d.driver}</div><div className="text-[12px] text-muted">{d.description} ({d.license})</div></td>
                    <td className="tabular px-3 py-2 font-mono text-[12px]">{d.version ?? "—"}</td>
                    <td className="px-3 py-2">
                      <Badge tone={DRIVER_TONE[d.status] ?? "neutral"}>{DRIVER_LABEL[d.status] ?? d.status}</Badge>
                      {d.error && <div className="mt-1 max-w-[280px] text-[12px] text-muted">{d.error}</div>}
                      {d.remediation && <div className="mt-1 max-w-[320px] text-[12px] font-medium text-crit">{d.remediation}</div>}
                    </td>
                    <td className="px-3 py-2 text-[12px] text-text-2">
                      {d.client_dependency ?? <span className="text-muted">None (pure Python / bundled)</span>}
                      {d.details && Object.keys(d.details).length > 0 && (
                        <div className="mt-1 font-mono text-2xs text-muted">{Object.entries(d.details).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") || "none" : String(v)}`).join(" · ")}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        </div>
      )}
    </div>
  );
}

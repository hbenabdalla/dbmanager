import { useQuery } from "@tanstack/react-query";
import { deploymentText, type AboutDeployment } from "@/features/platform/deployment";
import { BookOpen, ExternalLink, Github, Map as MapIcon } from "lucide-react";
import { COMPANY_NAME, KbmCoverMotif, KbmLogo, PRODUCT_TAGLINE } from "@/components/brand";
import { get } from "@/api/client";
import { KeyValue, PageHeader } from "@/components/page";
import { DatabaseEngineIcon } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { SUPPORT_LABEL } from "@/lib/capabilities";

export interface About {
  product: string;
  description: string;
  version: { application: string; backend: string; build: string | null; git_commit: string | null; build_date: string | null; schema: string | null };
  milestone: { version: string; name: string };
  license: string;
  links: { documentation: string | null; github: string | null; roadmap: string | null };
  engines: { engine: string; display_name: string; support_level: string; validation: string | null; validated_version: string | null }[];
  status: { api: string; database: string; timeseries: string; background_jobs: string };
  deployment?: AboutDeployment | null;
}

export const FRONTEND_VERSION = __APP_VERSION__;
export const FRONTEND_BUILD = import.meta.env.VITE_BUILD_VERSION || null;

export function useAbout() {
  return useQuery({ queryKey: ["about"], queryFn: () => get<About>("/about"), staleTime: 10 * 60_000 });
}

const STATUS_TONE: Record<string, "ok" | "warn" | "crit" | "neutral"> = {
  ok: "ok", running: "ok", running_elsewhere: "ok", unavailable: "crit", stopped: "warn", no_worker: "warn", disabled: "neutral",
};
const VALIDATION: Record<string, { label: string; tone: "ok" | "warn" | "crit" | "neutral" }> = {
  validated: { label: "Validated", tone: "ok" },
  failed: { label: "Validation failed", tone: "crit" },
  pending: { label: "Validation pending", tone: "warn" },
  external: { label: "External validation required", tone: "neutral" },
};

function LinkRow({ icon: Icon, label, href, missing }: { icon: typeof BookOpen; label: string; href: string | null; missing: string }) {
  return (
    <div className="flex items-center gap-2.5 text-[13px]">
      <Icon className="size-4 text-muted" />
      <span className="w-28 text-muted">{label}</span>
      {href ? (
        <a href={href} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 break-all text-accent hover:underline">
          {href} <ExternalLink className="size-3" />
        </a>
      ) : <span className="text-muted">{missing}</span>}
    </div>
  );
}

export function AboutPage() {
  const q = useAbout();
  if (q.isLoading) return <SkeletonRows rows={8} />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const a = q.data;
  const v = a.version;
  return (
    <div>
      <PageHeader title={`About ${a.product}`} description={a.description} crumbs={[{ label: "Platform" }, { label: "About" }]} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="relative overflow-hidden border-0 bg-brand text-white lg:col-span-2 dark:bg-brand-navy">
          <KbmCoverMotif className="opacity-70" />
          <CardBody className="relative flex flex-wrap items-center gap-8 py-7">
            <KbmLogo full width={190} tone="white" label="KBM — Khorafi Business Machines" />
            <div className="min-w-0 max-w-xl space-y-1.5 text-[13px]">
              <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-white/70">{PRODUCT_TAGLINE}</p>
              <p className="text-[24px] font-bold tracking-[-0.01em]">{a.product} <span className="font-mono text-[15px] font-medium text-white/80">{v.application}</span></p>
              <p className="text-white/85">{a.description}</p>
              <p className="text-white/85">A product of {COMPANY_NAME}.</p>
              <p className="text-[12px] text-white/65">
                PostgreSQL, MySQL, MariaDB, SQL Server, Oracle and Db2 are trademarks of their respective owners. KBM DBManager is not affiliated with or
                endorsed by them; the engine marks shown in KBM DBManager are its own and are not the vendors' logos.
              </p>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Version" description={`Milestone ${a.milestone.version} — ${a.milestone.name}`} />
          <CardBody>
            <KeyValue items={[
              ["Application", <span className="font-mono">{v.application}</span>],
              ["Backend", <span className="font-mono">{v.backend}</span>],
              ["Frontend", <span className="font-mono">{FRONTEND_VERSION}{FRONTEND_BUILD ? ` (${FRONTEND_BUILD})` : ""}</span>],
              ["Build", v.build ? <span className="font-mono">{v.build}</span> : <span className="text-muted">source build</span>],
              ["Git commit", v.git_commit ? <span className="font-mono">{v.git_commit}</span> : <span className="text-muted">not recorded</span>],
              ["Build date", v.build_date ?? <span className="text-muted">not recorded</span>],
              ["Database schema", <span className="font-mono">{v.schema ?? "unknown"}</span>],
              ["License", a.license],
            ]} />
            {FRONTEND_VERSION !== v.application && (
              <p role="alert" className="mt-3 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[12.5px] text-warn">
                The frontend ({FRONTEND_VERSION}) and backend ({v.application}) versions differ: rebuild both images from the same release.
              </p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Backend status" />
          <CardBody className="space-y-2">
            {([["API", a.status.api], ["Application database", a.status.database], ["Time-series database", a.status.timeseries], ["Collectors & scheduler", a.status.background_jobs]] as const).map(([k, s]) => (
              <div key={k} className="flex items-center justify-between text-[13px]">
                <span>{k}</span><Badge tone={STATUS_TONE[s] ?? "neutral"}>{s.replaceAll("_", " ")}</Badge>
              </div>
            ))}
            {a.deployment && (
              <div className="flex items-center justify-between text-[13px]" data-testid="about-deployment">
                <span>Deployment</span><span className="tabular text-text-2">{deploymentText(a.deployment)}</span>
              </div>
            )}
          </CardBody>
          <CardHeader title="Links" className="border-t border-border" />
          <CardBody className="space-y-2">
            <LinkRow icon={BookOpen} label="Documentation" href={a.links.documentation} missing="hidden (DBM_DOCUMENTATION_URL=off)" />
            <LinkRow icon={Github} label="Source code" href={a.links.github} missing="not configured (DBM_GITHUB_URL)" />
            <LinkRow icon={MapIcon} label="Roadmap" href={a.links.roadmap} missing="not configured (DBM_ROADMAP_URL)" />
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Supported database engines" description="Support level of each adapter and the result of the real-engine validation suite shipped with this build" />
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead><tr className="border-b border-border text-left text-[12px] text-muted"><th className="px-4 py-2 font-medium">Engine</th><th className="px-4 py-2 font-medium">Support</th><th className="px-4 py-2 font-medium">Real-engine validation</th></tr></thead>
              <tbody>
                {a.engines.map((e) => {
                  const val = e.validation ? VALIDATION[e.validation] : null;
                  return (
                    <tr key={e.engine} className="border-b border-border last:border-0">
                      <td className="px-4 py-2"><DatabaseEngineIcon engine={e.engine} withLabel /></td>
                      <td className="px-4 py-2"><Badge tone={SUPPORT_LABEL[e.support_level]?.tone ?? "neutral"}>{SUPPORT_LABEL[e.support_level]?.label ?? e.support_level}</Badge></td>
                      <td className="px-4 py-2">{val ? <><Badge tone={val.tone}>{val.label}</Badge>{e.validated_version && <span className="ml-2 font-mono text-[12px] text-muted">{e.validated_version}</span>}</> : <span className="text-muted">—</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}

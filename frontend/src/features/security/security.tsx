import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import type { Page } from "@/api/types";
import { Meter } from "@/components/charts";
import { PageHeader } from "@/components/page";
import { DatabaseEngineIcon, SeverityBadge, SimulatedBadge } from "@/components/status";
import { useSourceParam } from "@/lib/fleet-source";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Input, Select } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { ENGINES } from "@/lib/engines";
import { relTime } from "@/lib/format";
import { FindingsList } from "../shared/findings";

interface Posture { score: number | null; by_severity: Record<string, number>; by_category: Record<string, number>; servers: { server_id: string; server_name: string; engine: string; is_demo: boolean; environment: string | null; score: number; by_severity: Record<string, number> }[] }

const scoreTone = (s: number) => (s >= 85 ? "var(--ok)" : s >= 60 ? "var(--warn)" : "var(--crit)");
const BANDS = [
  { key: "good", label: "Good", range: "85–100", test: (s: number) => s >= 85, color: "var(--ok)" },
  { key: "fair", label: "Fair", range: "60–84", test: (s: number) => s >= 60 && s < 85, color: "var(--warn)" },
  { key: "poor", label: "Poor", range: "0–59", test: (s: number) => s < 60, color: "var(--crit)" },
];

/** Half-circle gauge for a 0–100 score. */
function ScoreGauge({ score }: { score: number | null }) {
  const r = 52;
  const c = Math.PI * r;
  const v = score ?? 0;
  return (
    <svg viewBox="0 0 128 76" className="w-[180px]" role="img" aria-label={score === null ? "No score yet" : `Fleet security score ${score} of 100`}>
      <path d="M12 68 A52 52 0 0 1 116 68" fill="none" stroke="var(--surface-3)" strokeWidth="10" strokeLinecap="round" />
      {score !== null && (
        <path d="M12 68 A52 52 0 0 1 116 68" fill="none" stroke={scoreTone(v)} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={`${(v / 100) * c} ${c}`} style={{ transition: "stroke-dasharray var(--dur-slow) var(--ease-out)" }} />
      )}
      <text x="64" y="60" textAnchor="middle" className="tabular" style={{ fontSize: 26, fontWeight: 600, fill: score === null ? "var(--muted)" : scoreTone(v) }}>{score ?? "—"}</text>
      <text x="64" y="74" textAnchor="middle" style={{ fontSize: 9, fill: "var(--muted)" }}>of 100</text>
    </svg>
  );
}

function Breakdown({ title, rows }: { title: string; rows: { key: string; label: React.ReactNode; avg: number; n: number }[] }) {
  return (
    <Card>
      <CardHeader title={title} description="Average score and server count" />
      <CardBody className="space-y-2.5">
        {rows.length === 0 && <p className="text-[13px] text-muted">No servers.</p>}
        {rows.map((r) => (
          <div key={r.key} className="text-[13px]">
            <div className="mb-1 flex items-center justify-between gap-2"><span className="min-w-0 truncate">{r.label}</span><span className="tabular"><span className="font-semibold" style={{ color: scoreTone(r.avg) }}>{r.avg}</span> <span className="text-muted">· {r.n}</span></span></div>
            <Meter value={r.avg} tone={scoreTone(r.avg)} />
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function groupAvg<T extends { score: number }>(items: T[], key: (x: T) => string) {
  const m = new Map<string, number[]>();
  for (const i of items) m.set(key(i), [...(m.get(key(i)) ?? []), i.score]);
  return [...m.entries()].map(([k, v]) => ({ key: k, avg: Math.round(v.reduce((a, b) => a + b, 0) / v.length), n: v.length })).sort((a, b) => a.avg - b.avg);
}

export function PosturePage() {
  const source = useSourceParam();
  const q = useQuery({ queryKey: ["posture", source], queryFn: () => get<Posture>("/security/posture", { source }) });
  const navigate = useNavigate();
  const [band, setBand] = useState<string | null>(null);
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const p = q.data;
  const columns: ColumnDef<Posture["servers"][number], unknown>[] = [
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: s } }) => <span className="flex items-center gap-2"><DatabaseEngineIcon engine={s.engine} /><span className="font-medium">{s.server_name}</span>{s.is_demo && <SimulatedBadge />}</span> },
    { header: "Environment", accessorKey: "environment" },
    { header: "Score", accessorKey: "score", meta: { align: "right" }, cell: ({ getValue }) => { const v = getValue() as number; return <div className="ml-auto w-28"><div className="text-right font-semibold tabular" style={{ color: scoreTone(v) }}>{v}</div><Meter value={v} tone={scoreTone(v)} /></div>; } },
    ...["critical", "high", "medium", "low"].map((sev) => ({ header: sev[0].toUpperCase() + sev.slice(1), id: sev, accessorFn: (s: Posture["servers"][number]) => s.by_severity[sev] ?? 0, meta: { align: "right" } } as ColumnDef<Posture["servers"][number], unknown>)),
  ];
  const servers = p?.servers ?? [];
  const shown = band ? servers.filter((s) => BANDS.find((b) => b.key === band)!.test(s.score)) : servers;
  const cats = p ? Object.entries(p.by_category).sort((a, b) => b[1] - a[1]) : [];
  const catMax = Math.max(1, ...cats.map(([, n]) => n));
  return (
    <div>
      <PageHeader title="Security posture" description="A score per server from open findings (critical 25, high 10, medium 4, low 1 point deducted)." crumbs={[{ label: "Security" }, { label: "Posture" }]} />
      {!p ? <SkeletonRows /> : servers.length === 0 ? (
        <Card><CardBody><p className="text-[13px] text-muted">No servers in scope yet. Scores appear after the first security scan of a registered server.</p></CardBody></Card>
      ) : (
        <div className="anim-fade space-y-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(260px,320px)_1fr]">
            <Card>
              <CardBody className="flex flex-col items-center gap-2">
                <ScoreGauge score={p.score} />
                <div className="text-[12.5px] text-muted">Fleet score: average of {servers.length} server{servers.length === 1 ? "" : "s"}</div>
                <div className="mt-1 flex w-full overflow-hidden rounded-full" role="group" aria-label="Score bands">
                  {BANDS.map((b) => {
                    const n = servers.filter((s) => b.test(s.score)).length;
                    return n ? <div key={b.key} style={{ width: `${(n / servers.length) * 100}%`, background: b.color }} className="h-2" title={`${b.label}: ${n}`} /> : null;
                  })}
                </div>
                <div className="flex w-full justify-between gap-1">
                  {BANDS.map((b) => {
                    const n = servers.filter((s) => b.test(s.score)).length;
                    return (
                      <button key={b.key} type="button" aria-pressed={band === b.key} onClick={() => setBand(band === b.key ? null : b.key)}
                        className={`flex-1 rounded-md border px-2 py-1.5 text-left text-[12px] transition-colors duration-150 ${band === b.key ? "border-accent bg-accent-soft/60" : "border-border hover:border-border-strong"}`}>
                        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: b.color }} />{b.label}</span>
                        <span className="tabular text-[15px] font-semibold">{n}</span> <span className="text-2xs text-muted">{b.range}</span>
                      </button>
                    );
                  })}
                </div>
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Open findings" description="Select a severity or category to review those findings" />
              <CardBody className="grid gap-5 md:grid-cols-2">
                <div className="grid grid-cols-5 content-start gap-2 self-start">
                  {["critical", "high", "medium", "low", "info"].map((sev) => (
                    <button key={sev} type="button" onClick={() => navigate(`/security/findings?severity=${sev}`)}
                      className="lift rounded-md border border-border px-2 py-2 text-left hover:border-border-strong">
                      <SeverityBadge severity={sev} />
                      <div className="tabular mt-1 text-[20px] font-semibold" style={{ color: (sev === "critical" || sev === "high") && p.by_severity[sev] ? "var(--crit)" : undefined }}>{p.by_severity[sev] ?? 0}</div>
                    </button>
                  ))}
                </div>
                <ul className="space-y-1.5">
                  {cats.length === 0 && <li className="text-[13px] text-muted">No open findings.</li>}
                  {cats.map(([c, n]) => (
                    <li key={c}>
                      <button type="button" onClick={() => navigate(`/security/findings?category=${encodeURIComponent(c)}`)} className="w-full rounded px-1 py-0.5 text-left text-[13px] hover:bg-surface-2">
                        <span className="flex justify-between"><span className="capitalize">{c}</span><span className="tabular text-text-2">{n}</span></span>
                        <span className="mt-0.5 block h-1.5 overflow-hidden rounded-full bg-surface-3"><span className="block h-full rounded-full bg-[var(--crit)]/80" style={{ width: `${(n / catMax) * 100}%` }} /></span>
                      </button>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Breakdown title="By environment" rows={groupAvg(servers, (s) => s.environment ?? "Unassigned").map((r) => ({ ...r, label: r.key }))} />
            <Breakdown title="By engine" rows={groupAvg(servers, (s) => s.engine).map((r) => ({ ...r, label: <DatabaseEngineIcon engine={r.key} withLabel /> }))} />
          </div>
          <Card>
            <CardHeader title={band ? `Servers rated ${BANDS.find((b) => b.key === band)!.label.toLowerCase()}` : "Servers, lowest score first"}
              actions={band ? <button className="text-[12.5px] text-accent hover:underline" onClick={() => setBand(null)}>Show all</button> : undefined} />
            <DataTable columns={columns} data={shown} dense onRowClick={(s) => navigate(`/servers/${s.server_id}?tab=security`)} initialSort={[{ id: "score", desc: false }]} />
          </Card>
        </div>
      )}
    </div>
  );
}

export function SecurityFindingsPage() {
  return (
    <div>
      <PageHeader title="Security findings" description="Accounts, privileges, authentication, encryption and auditing issues found by the security rules." crumbs={[{ label: "Security" }, { label: "Findings" }]} />
      <FindingsList kind="security" />
    </div>
  );
}

interface Account { server_id: string; server_name: string; engine: string; is_demo: boolean; environment: string | null; name: string; kind: string; can_login: boolean; is_admin: boolean; admin_reasons: string[]; member_of: string[]; direct_grant_count: number; last_login_at: string | null; seen_at: string }

export function PrivilegedAccountsPage() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("admin");
  const [page, setPage] = useState(1);
  const source = useSourceParam();
  const data = useQuery({
    queryKey: ["privileged", source, q, mode, page],
    queryFn: () => get<Page<Account>>("/access/privileged-accounts", { q, source, only_admin: mode !== "all", dormant_days: mode === "dormant" ? 90 : undefined, page, page_size: 50 }),
    placeholderData: (p) => p,
  });
  const navigate = useNavigate();
  const columns: ColumnDef<Account, unknown>[] = [
    { header: "Account", accessorKey: "name", cell: ({ row: { original: a } }) => <span className="font-medium">{a.name}{!a.can_login && <Badge className="ml-2">role</Badge>}</span> },
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: a } }) => <span className="flex items-center gap-2 whitespace-nowrap"><DatabaseEngineIcon engine={a.engine} />{a.server_name}{a.is_demo && <SimulatedBadge />}</span> },
    { header: "Environment", accessorKey: "environment" },
    { header: "Why it's privileged", accessorFn: (a) => a.admin_reasons.join(", "), cell: ({ row: { original: a } }) => <span className="flex flex-wrap gap-1">{a.admin_reasons.map((r) => <Badge key={r} tone="crit"><ShieldAlert className="size-3" />{r}</Badge>)}</span> },
    { header: "Direct grants", accessorKey: "direct_grant_count", meta: { align: "right" } },
    { header: "Last login", accessorKey: "last_login_at", cell: ({ getValue }) => getValue() ? relTime(getValue() as string) : <span className="text-muted">not tracked</span> },
    { header: "Scanned", accessorKey: "seen_at", cell: ({ getValue }) => <span className="text-[12px] text-muted">{relTime(getValue() as string)}</span> },
  ];
  return (
    <div>
      <PageHeader title="Privileged accounts" description="Administrative accounts across the fleet from the latest security scan, without connecting live to every server."
        crumbs={[{ label: "Security" }, { label: "Privileged accounts" }]} />
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <Input aria-label="Account" className="w-60" placeholder="Account name" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          <Select aria-label="Show" className="w-64" value={mode} onChange={(e) => { setMode(e.target.value); setPage(1); }}>
            <option value="admin">Administrative accounts</option><option value="dormant">Administrative, no login in 90 days</option><option value="all">All accounts and roles</option>
          </Select>
          <span className="ml-auto text-[12.5px] text-muted tabular">{data.data?.total ?? 0} accounts</span>
        </div>
        <DataTable columns={columns} data={data.data?.items} loading={data.isLoading} error={data.error} dense onRowClick={(a) => navigate(`/servers/${a.server_id}?tab=users`)}
          manual={data.data ? { page, pageSize: 50, total: data.data.total, onPage: setPage } : undefined} />
      </Card>
    </div>
  );
}

interface Rule { rule_id: string; engines: string[]; kind: string; category: string; severity: string; title: string; description: string }

export function CompliancePage() {
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => get<Rule[]>("/advisor/rules") });
  const source = useSourceParam();
  const open = useQuery({ queryKey: ["security", "compliance-open", source], queryFn: () => get<Page<{ rule_id: string }>>("/security/findings", { page_size: 500, source }) });
  const [kind, setKind] = useState("security");
  const counts = (open.data?.items ?? []).reduce<Record<string, number>>((a, f) => ({ ...a, [f.rule_id]: (a[f.rule_id] ?? 0) + 1 }), {});
  const list = (rules.data ?? []).filter((r) => r.kind === kind);
  return (
    <div>
      <PageHeader title="Compliance" description="The deterministic rule catalog that produces findings, with how many servers currently fail each check."
        crumbs={[{ label: "Security" }, { label: "Compliance" }]}
        actions={<Select aria-label="Rule set" className="w-52" value={kind} onChange={(e) => setKind(e.target.value)}><option value="security">Security rules</option><option value="performance">Performance rules</option></Select>} />
      <Card>
        <DataTable data={list} loading={rules.isLoading} dense pageSize={100} columns={[
          { header: "Rule", accessorKey: "rule_id", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
          { header: "Check", accessorKey: "title", cell: ({ row: { original: r } }) => <div><div className="font-medium">{r.title}</div>{r.description && <div className="max-w-[520px] truncate text-[12px] text-muted">{r.description}</div>}</div> },
          { header: "Engines", accessorFn: (r) => r.engines.join(","), cell: ({ row: { original: r } }) => r.engines.includes("*") ? <Badge>all engines</Badge> : <span className="flex gap-1">{r.engines.map((e) => <DatabaseEngineIcon key={e} engine={e} />)}</span> },
          { header: "Category", accessorKey: "category", cell: ({ getValue }) => <span className="capitalize">{getValue() as string}</span> },
          { header: "Default severity", accessorKey: "severity", cell: ({ getValue }) => <SeverityBadge severity={getValue() as string} /> },
          ...(kind === "security" ? [{ header: "Failing", id: "failing", accessorFn: (r: Rule) => counts[r.rule_id] ?? 0, meta: { align: "right" }, cell: ({ getValue }: { getValue: () => unknown }) => { const n = getValue() as number; return n ? <Badge tone="crit">{n}</Badge> : <Badge tone="ok">pass</Badge>; } } as ColumnDef<Rule, unknown>] : []),
        ] as ColumnDef<Rule, unknown>[]} />
      </Card>
      <p className="mt-3 text-[12px] text-muted">Engines covered: {Object.values(ENGINES).map((e) => e.label).join(", ")}.</p>
    </div>
  );
}

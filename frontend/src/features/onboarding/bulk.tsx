/** Bulk environment onboarding: manual grid, CSV upload or JSON paste → per-row validation with
 * in-place fixing → create with per-row results. Passwords are never accepted in bulk files. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Download, FileJson, FileSpreadsheet, Plus, Table2, Trash2, Upload, XCircle } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, buildUrl, post } from "@/api/client";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export const BULK_COLUMNS = [
  "name", "engine", "host", "port", "environment", "site", "cluster", "application", "default_database", "tls_mode",
  "management_mode", "credential", "tags", "description",
] as const;
const REQUIRED = new Set(["name", "engine", "host", "environment"]);
const SECRET_KEY = /pass(word|wd)?|pwd|secret|token|(^|_)key$/i;
export const SECRET_MESSAGE = "Never put passwords in bulk files: reference the secret in the credential column instead";
/** Accepted forms of the credential column (the backend resolves them; nothing secret is in the file). */
export const CREDENTIAL_HELP: [string, string][] = [
  ["server:<registered server>", "reuse that server's monitoring credential"],
  ["env:<VARIABLE>@<username>", "password read by the backend from an environment variable (DBM_*, PG*, AWS_* names are refused)"],
  ["file:<path>@<username>", "password read by the backend from a mounted secret file"],
];

export type Row = Record<string, string>;
interface Issue { field: string; message: string }
interface RowResult { index: number; ok: boolean; errors: Issue[]; warnings: Issue[]; normalized?: Record<string, unknown> }
interface Validation { rows: RowResult[]; summary: { total: number; valid: number; invalid: number; warnings: number } }
interface CreateResult { results: { index: number; ok: boolean; server_id?: string; name: string; error?: string }[]; created: number; failed: number }

/** RFC 4180-ish CSV: quoted fields, escaped quotes, CRLF, embedded newlines. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let q = false;
  const s = text.replace(/^﻿/, "");
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) {
      if (c === '"' && s[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((x) => x.trim() !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some((x) => x.trim() !== "")) rows.push(row);
  return rows;
}

/** Turns parsed input into rows; rejects secret-looking columns before anything leaves the browser. */
export function toRows(records: Record<string, unknown>[]): { rows: Row[]; rejected: string[] } {
  const rejected = new Set<string>();
  const rows = records.map((r) => {
    const out: Row = {};
    for (const [k, v] of Object.entries(r)) {
      const key = k.trim().toLowerCase();
      if (SECRET_KEY.test(key)) { rejected.add(k); continue; }
      out[key] = v == null ? "" : typeof v === "object" ? (key === "tags" ? Object.entries(v as Record<string, unknown>).map(([a, b]) => `${a}=${b}`).join(";") : JSON.stringify(v)) : String(v);
    }
    return out;
  });
  return { rows, rejected: [...rejected] };
}

export function csvToRecords(text: string): Record<string, string>[] {
  const [head, ...body] = parseCsv(text);
  if (!head) return [];
  return body.map((cells) => Object.fromEntries(head.map((h, i) => [h.trim(), (cells[i] ?? "").trim()])));
}

const blank = (): Row => Object.fromEntries(BULK_COLUMNS.map((c) => [c, c === "management_mode" ? "monitoring_only" : ""]));

export function BulkOnboardingPage() {
  const { can } = useAuth();
  const [mode, setMode] = useState<"grid" | "csv" | "json">("grid");
  const [rows, setRows] = useState<Row[]>([blank()]);
  const [json, setJson] = useState("");
  const [validation, setValidation] = useState<Validation | null>(null);
  const [monitoring, setMonitoring] = useState(true);
  const [created, setCreated] = useState<CreateResult | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const allowed = can("server.register");

  const load = (records: Record<string, unknown>[]) => {
    const { rows: r, rejected } = toRows(records);
    if (rejected.length) toast.error(SECRET_MESSAGE, { description: `Ignored column(s): ${rejected.join(", ")}` });
    setRows(r.length ? r : [blank()]); setValidation(null); setCreated(null); setMode("grid");
  };

  const onFile = async (file: File) => {
    const text = await file.text();
    if (file.name.toLowerCase().endsWith(".json")) loadJson(text);
    else load(csvToRecords(text));
  };
  const loadJson = (text: string) => {
    try {
      const v = JSON.parse(text) as unknown;
      const arr = Array.isArray(v) ? v : Array.isArray((v as { servers?: unknown }).servers) ? (v as { servers: unknown[] }).servers : null;
      if (!arr) throw new Error("expected an array of server objects (or {\"servers\": [...]})");
      load(arr as Record<string, unknown>[]);
    } catch (e) { toast.error(`Invalid JSON: ${(e as Error).message}`); }
  };

  const columns = [...new Set<string>([...BULK_COLUMNS, ...rows.flatMap((r) => Object.keys(r))])];
  const issueFor = (i: number, field: string) => {
    const r = validation?.rows.find((x) => x.index === i);
    return { error: r?.errors.find((e) => e.field === field)?.message, warning: r?.warnings.find((e) => e.field === field)?.message };
  };
  const payload = () => rows.map((r) => Object.fromEntries(Object.entries(r).filter(([, v]) => v !== "")));

  const validate = useMutation({
    mutationFn: () => post<Validation>("/servers/bulk/validate", { rows: payload() }),
    onSuccess: (v) => { setValidation(v); setCreated(null); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const create = useMutation({
    mutationFn: async () => {
      setProgress(0);
      const timer = setInterval(() => setProgress((p) => (p == null ? p : Math.min(90, p + 7))), 300);
      try { return await post<CreateResult>("/servers/bulk/create", { rows: payload(), monitoring_enabled: monitoring }); }
      finally { clearInterval(timer); }
    },
    onSuccess: (r) => {
      setProgress(100); setCreated(r);
      toast[r.failed ? "warning" : "success"](`${r.created} server(s) registered${r.failed ? `, ${r.failed} failed` : ""}`);
      for (const k of ["servers", "fleet-sources", "dashboard", "setup-checklist"]) qc.invalidateQueries({ queryKey: [k] });
    },
    onError: (e: ApiError) => { setProgress(null); toast.error(e.message); },
  });

  const edit = (i: number, k: string, v: string) => {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
    // a fixed field clears its own error immediately; the row is revalidated on the next Validate
    setValidation((val) => val && { ...val, rows: val.rows.map((r) => (r.index === i ? { ...r, errors: r.errors.filter((e) => e.field !== k), warnings: r.warnings.filter((e) => e.field !== k) } : r)) });
  };
  const s = validation?.summary;
  const canCreate = !!validation && validation.summary.valid > 0 && !create.isPending;

  return (
    <div>
      <PageHeader title="Bulk onboarding" crumbs={[{ label: "Fleet", to: "/fleet/servers" }, { label: "Bulk onboarding" }]}
        description="Register many servers at once from a grid, a CSV file or JSON. Bulk files never carry passwords: the credential column references where the backend finds the secret."
        actions={<Button asChild><a href={buildUrl("/servers/bulk/template.csv")} download="servers-template.csv"><Download /> CSV template</a></Button>} />
      {!allowed ? <Card><EmptyState title="You can't register servers">Registering servers needs the server.register permission.</EmptyState></Card> : (
        <div className="space-y-4">
          <Card>
            <CardHeader title="1. Provide servers" actions={
              <div role="tablist" aria-label="Input method" className="flex gap-1">
                {([["grid", Table2, "Grid"], ["csv", FileSpreadsheet, "CSV file"], ["json", FileJson, "JSON"]] as const).map(([k, Icon, l]) => (
                  <Button key={k} size="sm" role="tab" aria-selected={mode === k} variant={mode === k ? "primary" : "ghost"} onClick={() => setMode(k)}><Icon /> {l}</Button>
                ))}
              </div>} />
            <CardBody>
              {mode === "csv" && (
                <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border-strong px-4 py-8 text-center"
                  onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) void onFile(f); }}>
                  <Upload className="size-6 text-muted" aria-hidden />
                  <p className="text-[13px]">Drop a CSV or JSON file here, or</p>
                  <Button onClick={() => fileRef.current?.click()}>Choose file</Button>
                  <input ref={fileRef} type="file" accept=".csv,.json,text/csv,application/json" className="sr-only" aria-label="Upload file"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) void onFile(f); e.target.value = ""; }} />
                  <p className="text-[12px] text-muted">Columns: {BULK_COLUMNS.join(", ")}. Password columns are rejected.</p>
                </div>
              )}
              {mode === "json" && (
                <div className="space-y-2">
                  <Textarea aria-label="JSON servers" rows={8} value={json} onChange={(e) => setJson(e.target.value)}
                    placeholder={'[{"name": "pg-01", "engine": "postgresql", "host": "10.0.0.5", "port": 5432, "environment": "Production", "credential": "pg-monitor"}]'} />
                  <Button onClick={() => loadJson(json)} disabled={!json.trim()}>Load into grid</Button>
                </div>
              )}
              {mode === "grid" && (
                <div className="space-y-2">
                  <div className="scroll-thin overflow-x-auto rounded-md border border-border">
                    <table className="w-full min-w-[1100px] border-collapse text-[12.5px]">
                      <thead><tr className="bg-surface-2 text-left text-muted">
                        <th className="w-10 px-2 py-1.5 font-medium">#</th>
                        {columns.map((c) => <th key={c} className="px-1.5 py-1.5 font-medium">{c}{REQUIRED.has(c) && <span className="text-crit" aria-hidden>*</span>}</th>)}
                        <th className="w-8" />
                      </tr></thead>
                      <tbody>
                        {rows.map((r, i) => {
                          const res = validation?.rows.find((x) => x.index === i);
                          const general = res?.errors.filter((e) => !columns.includes(e.field)) ?? [];
                          return (
                            <tr key={i} className={cn("border-t border-border align-top", res && !res.ok && "bg-crit-soft/30")}>
                              <td className="px-2 py-1 tabular text-muted">
                                <span className="flex items-center gap-1">{i + 1}{res && (res.ok ? <CheckCircle2 className="size-3.5 text-ok" aria-label="valid" /> : <XCircle className="size-3.5 text-crit" aria-label="invalid" />)}</span>
                                {general.map((g) => <span key={g.message} className="block max-w-[120px] text-2xs text-crit">{g.message}</span>)}
                              </td>
                              {columns.map((c) => {
                                const { error, warning } = issueFor(i, c);
                                return (
                                  <td key={c} className="px-1 py-1">
                                    <input aria-label={`Row ${i + 1} ${c}`} aria-invalid={error ? true : undefined} title={error ?? warning}
                                      value={r[c] ?? ""} onChange={(e) => edit(i, c, e.target.value)}
                                      className={cn("h-7 w-full min-w-[90px] rounded border bg-surface px-1.5 text-[12.5px] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]",
                                        error ? "border-crit" : warning ? "border-warn" : "border-border")} />
                                    {error && <span className="mt-0.5 block text-2xs text-crit">{error}</span>}
                                    {!error && warning && <span className="mt-0.5 block text-2xs text-warn">{warning}</span>}
                                  </td>
                                );
                              })}
                              <td className="px-1 py-1"><Button size="icon" variant="ghost" aria-label={`Remove row ${i + 1}`} onClick={() => { setRows((rs) => rs.filter((_, j) => j !== i)); setValidation(null); }}><Trash2 /></Button></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <Button size="sm" onClick={() => { setRows((rs) => [...rs, blank()]); setValidation(null); }}><Plus /> Add row</Button>
                </div>
              )}
              <details className="mt-3 text-[12.5px] text-muted">
                <summary className="cursor-pointer font-medium text-text-2">The credential column</summary>
                <ul className="mt-1 space-y-0.5">
                  {CREDENTIAL_HELP.map(([f, d]) => <li key={f}><code className="font-mono text-[12px] text-text">{f}</code> — {d}</li>)}
                  <li>Leave it empty to register without a credential and add one later. Using credentials needs the credentials.manage permission.</li>
                </ul>
              </details>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="2. Validate and register" description="Rows are checked by the server: engine, address, duplicates, environment/site/cluster names and the credential reference." />
            <CardBody className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <Button onClick={() => validate.mutate()} disabled={validate.isPending || !rows.length}>{validate.isPending ? "Validating…" : "Validate rows"}</Button>
                {s && <span className="flex flex-wrap gap-1.5 text-[12.5px]" role="status"><Badge tone="ok">{s.valid} valid</Badge>{s.invalid > 0 && <Badge tone="crit">{s.invalid} invalid</Badge>}{s.warnings > 0 && <Badge tone="warn">{s.warnings} with warnings</Badge>}</span>}
                <label className="ml-auto flex items-center gap-2 text-[13px]"><Switch checked={monitoring} onCheckedChange={setMonitoring} label="Start monitoring" /> Start monitoring</label>
                <Button variant="primary" onClick={() => create.mutate()} disabled={!canCreate}>Register {s?.valid ?? 0} server(s)</Button>
              </div>
              {s && s.invalid > 0 && <p className="flex items-center gap-1.5 text-[12.5px] text-muted"><AlertTriangle className="size-4 text-warn" aria-hidden /> Invalid rows are skipped. Fix them in the grid and validate again.</p>}
              {progress != null && (
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label="Registration progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
                  <div className="h-full bg-accent transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${progress}%` }} />
                </div>
              )}
              {created && (
                <ul className="divide-y divide-border rounded-md border border-border text-[13px]" aria-label="Registration results">
                  {created.results.map((r) => (
                    <li key={r.index} className="flex items-center gap-2 px-3 py-1.5">
                      {r.ok ? <CheckCircle2 className="size-4 text-ok" aria-hidden /> : <XCircle className="size-4 text-crit" aria-hidden />}
                      <span className="font-medium">{r.name || `Row ${r.index + 1}`}</span>
                      {r.ok && r.server_id ? <Link className="ml-auto text-accent hover:underline" to={`/servers/${r.server_id}`}>Open</Link> : <span className="ml-auto text-crit">{r.error}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}

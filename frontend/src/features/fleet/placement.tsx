import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, get, post, put } from "@/api/client";
import { ConfirmDialog } from "@/components/confirm";
import { PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";

export type PlacementKind = "environments" | "sites" | "clusters" | "applications";
type Item = { id: string; name: string; server_count?: number | null };
const val = (it: Item, k: string): unknown => (it as unknown as Record<string, unknown>)[k];

interface FieldDef { key: string; label: string; choices?: string[]; number?: boolean; hint?: string }

/** Fields accepted by the backend schemas (EnvironmentIn, SiteIn, ClusterIn, ApplicationIn). */
export const PLACEMENT: Record<PlacementKind, { singular: string; title: string; fields: FieldDef[]; defaults: Record<string, unknown> }> = {
  environments: {
    singular: "environment", title: "Environments",
    fields: [
      { key: "tier", label: "Tier", choices: ["production", "dr", "uat", "development", "test"], hint: "Production tier servers require typed confirmation for every change" },
      { key: "description", label: "Description" },
      { key: "sort_order", label: "Sort order", number: true },
    ],
    defaults: { tier: "development", description: "", sort_order: 100 },
  },
  sites: {
    singular: "site", title: "Sites",
    fields: [{ key: "region", label: "Region" }, { key: "provider", label: "Provider", hint: "e.g. on-premises, aws, azure, gcp" }],
    defaults: { region: "", provider: "" },
  },
  clusters: {
    singular: "cluster", title: "Clusters",
    fields: [
      { key: "kind", label: "Topology", choices: ["primary-replica", "rac", "availability-group", "galera", "hadr", "pacemaker"] },
      { key: "description", label: "Description" },
    ],
    defaults: { kind: "primary-replica", description: "" },
  },
  applications: {
    singular: "application", title: "Applications",
    fields: [{ key: "business_service", label: "Business service" }, { key: "owner", label: "Owner" }],
    defaults: { business_service: "", owner: "" },
  },
};

function payload(kind: PlacementKind, f: Record<string, unknown>) {
  const out: Record<string, unknown> = { name: String(f.name ?? "").trim() };
  for (const d of PLACEMENT[kind].fields) {
    const v = f[d.key];
    out[d.key] = d.number ? Number(v ?? 0) : d.choices ? v : (String(v ?? "").trim() || null);
  }
  return out;
}

export function PlacementDialog({ kind, item, onClose }: { kind: PlacementKind; item: Item | null; onClose: () => void }) {
  const spec = PLACEMENT[kind];
  const qc = useQueryClient();
  const [f, setF] = useState<Record<string, unknown>>(() => item ? { ...spec.defaults, ...(item as unknown as Record<string, unknown>) } : { name: "", ...spec.defaults });
  const m = useMutation({
    mutationFn: () => item ? put(`/${kind}/${item.id}`, payload(kind, f)) : post(`/${kind}`, payload(kind, f)),
    onSuccess: () => { toast.success(`${spec.singular[0].toUpperCase()}${spec.singular.slice(1)} ${item ? "saved" : "created"}`); qc.invalidateQueries({ queryKey: [kind] }); onClose(); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={item ? `Edit ${spec.singular} ${item.name}` : `New ${spec.singular}`}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!String(f.name ?? "").trim() || m.isPending} onClick={() => m.mutate()}>{item ? "Save" : "Create"}</Button></>}>
      <div className="space-y-3">
        <Field label="Name"><Input autoFocus value={String(f.name ?? "")} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        {spec.fields.map((d) => (
          <Field key={d.key} label={d.label} hint={d.hint}>
            {d.choices ? (
              <Select value={String(f[d.key] ?? "")} onChange={(e) => setF({ ...f, [d.key]: e.target.value })}>
                {d.choices.map((c) => <option key={c} value={c}>{c}</option>)}
              </Select>
            ) : (
              <Input type={d.number ? "number" : "text"} value={String(f[d.key] ?? "")} onChange={(e) => setF({ ...f, [d.key]: e.target.value })} />
            )}
          </Field>
        ))}
      </div>
    </Dialog>
  );
}

/** Edit and delete buttons for one placement item (used on the Environments and Clusters pages too). */
export function PlacementActions({ kind, item }: { kind: PlacementKind; item: Item }) {
  const spec = PLACEMENT[kind];
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const remove = useMutation({
    mutationFn: () => del(`/${kind}/${item.id}`),
    onSuccess: () => { toast.success(`${item.name} deleted`); setDeleting(false); qc.invalidateQueries({ queryKey: [kind] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <span className="inline-flex gap-1">
      <Button size="icon" variant="ghost" aria-label={`Edit ${item.name}`} onClick={() => setEditing(true)}><Pencil /></Button>
      <Button size="icon" variant="ghost" aria-label={`Delete ${item.name}`} onClick={() => setDeleting(true)}><Trash2 /></Button>
      {editing && <PlacementDialog kind={kind} item={item} onClose={() => setEditing(false)} />}
      <ConfirmDialog open={deleting} onOpenChange={setDeleting} title={`Delete ${spec.singular} ${item.name}?`} danger
        confirmLabel="Delete" busy={remove.isPending} onConfirm={() => remove.mutate()}>
        Only possible when no server references it{item.server_count ? ` (currently ${item.server_count})` : ""}. Move those servers first.
      </ConfirmDialog>
    </span>
  );
}

function PlacementTable({ kind }: { kind: PlacementKind }) {
  const spec = PLACEMENT[kind];
  const { canPlatform } = useAuth();
  const q = useQuery({ queryKey: [kind], queryFn: () => get<Item[]>(`/${kind}`), staleTime: 60_000 });
  const [creating, setCreating] = useState(false);
  const manage = canPlatform("inventory.manage");
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-border p-3">
        <span className="text-[13px] text-muted">{q.data?.length ?? 0} {spec.title.toLowerCase()}</span>
        {manage && <Button size="sm" variant="primary" onClick={() => setCreating(true)}><Plus /> New {spec.singular}</Button>}
      </div>
      {q.isLoading ? <div className="p-4"><SkeletonRows rows={3} /></div> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? (
        <EmptyState title={`No ${spec.title.toLowerCase()} yet`} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead><tr className="border-b border-border text-left text-[12px] text-muted">
              <th className="px-4 py-2 font-medium">Name</th>
              {spec.fields.map((d) => <th key={d.key} className="px-4 py-2 font-medium">{d.label}</th>)}
              <th className="px-4 py-2 font-medium">Servers</th>
              {manage && <th className="px-4 py-2"><span className="sr-only">Actions</span></th>}
            </tr></thead>
            <tbody>
              {q.data.map((it) => (
                <tr key={it.id} className="border-b border-border/60">
                  <td className="px-4 py-2 font-medium">{it.name}</td>
                  {spec.fields.map((d) => <td key={d.key} className="px-4 py-2 text-text-2">{val(it, d.key) === null || val(it, d.key) === undefined || val(it, d.key) === "" ? "—" : String(val(it, d.key))}</td>)}
                  <td className="px-4 py-2 tabular">{it.server_count ?? "—"}</td>
                  {manage && <td className="px-4 py-1 text-right"><PlacementActions kind={kind} item={it} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <PlacementDialog kind={kind} item={null} onClose={() => setCreating(false)} />}
    </Card>
  );
}

export function PlacementPage() {
  const [params, setParams] = useSearchParams();
  const kinds = Object.keys(PLACEMENT) as PlacementKind[];
  const tab = kinds.find((k) => k === params.get("tab")) ?? "environments";
  return (
    <div>
      <PageHeader title="Placement" crumbs={[{ label: "Fleet" }, { label: "Placement" }]}
        description="Environments, sites, clusters and applications that servers are assigned to. An item can only be deleted when no server uses it." />
      <Tabs value={tab} onValueChange={(v) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", v); return n; }, { replace: true })}>
        <TabsList className="mb-4">
          {(Object.keys(PLACEMENT) as PlacementKind[]).map((k) => <TabsTrigger key={k} value={k}>{PLACEMENT[k].title}</TabsTrigger>)}
        </TabsList>
        {(Object.keys(PLACEMENT) as PlacementKind[]).map((k) => <TabsContent key={k} value={k}><PlacementTable kind={k} /></TabsContent>)}
      </Tabs>
    </div>
  );
}

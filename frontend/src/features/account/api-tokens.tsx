import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError, del, get, post } from "@/api/client";
import { ConfirmDialog } from "@/components/confirm";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { dateTime, relTime } from "@/lib/format";

export interface ApiTokenRow {
  id: string; name: string; prefix: string; scopes: string[] | null; allowed_cidrs: string[] | null;
  created_at: string; expires_at: string; last_used_at: string | null; last_used_ip: string | null;
  revoked_at: string | null; created_by: string | null; status: "active" | "expired" | "revoked";
}
interface TokenPolicy { max_days: number; available_scopes: string[] }

const STATUS_TONE: Record<string, "ok" | "neutral" | "crit"> = { active: "ok", expired: "neutral", revoked: "crit" };
const EXPIRY_CHOICES = [7, 30, 90, 180, 365];

/** Token list used by the personal page and the administrator's user dialog. */
export function TokenTable({ tokens, onRevoke }: { tokens: ApiTokenRow[]; onRevoke?: (t: ApiTokenRow) => void }) {
  if (!tokens.length) return <EmptyState title="No API tokens">Tokens let scripts and integrations call the REST API as this user.</EmptyState>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-border text-left text-[12px] text-muted">
            <th className="px-3 py-2 font-medium">Name</th><th className="px-3 py-2 font-medium">Token</th>
            <th className="px-3 py-2 font-medium">Permissions</th><th className="px-3 py-2 font-medium">Expires</th>
            <th className="px-3 py-2 font-medium">Last used</th><th className="px-3 py-2 font-medium">Status</th><th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {tokens.map((t) => (
            <tr key={t.id} className="border-b border-border last:border-0">
              <td className="px-3 py-2 font-medium">{t.name}</td>
              <td className="px-3 py-2 font-mono text-[12px] text-muted">dbm_{t.prefix}_…</td>
              <td className="max-w-[260px] px-3 py-2 text-[12px] text-text-2">
                {t.scopes ? t.scopes.join(", ") : <span className="text-muted">All of the owner's permissions</span>}
                {t.allowed_cidrs && <div className="text-muted">From {t.allowed_cidrs.join(", ")}</div>}
              </td>
              <td className="px-3 py-2 tabular" title={dateTime(t.expires_at)}>{dateTime(t.expires_at)}</td>
              <td className="px-3 py-2 tabular">{t.last_used_at ? <span title={t.last_used_ip ?? ""}>{relTime(t.last_used_at)}</span> : <span className="text-muted">Never</span>}</td>
              <td className="px-3 py-2"><Badge tone={STATUS_TONE[t.status] ?? "neutral"}>{t.status}</Badge></td>
              <td className="px-3 py-2 text-right">
                {onRevoke && t.status === "active" && <Button size="sm" variant="ghost" onClick={() => onRevoke(t)} aria-label={`Revoke ${t.name}`}><Trash2 /> Revoke</Button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CreateTokenDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (token: string) => void }) {
  const policy = useQuery({ queryKey: ["token-policy"], queryFn: () => get<TokenPolicy>("/me/tokens/policy"), enabled: open });
  const [name, setName] = useState("");
  const [days, setDays] = useState(90);
  const [restrict, setRestrict] = useState(false);
  const [scopes, setScopes] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [cidrs, setCidrs] = useState("");
  const qc = useQueryClient();
  const choices = EXPIRY_CHOICES.filter((d) => d <= (policy.data?.max_days ?? 365));
  const available = useMemo(() => (policy.data?.available_scopes ?? []).filter((s) => !filter || s.includes(filter.toLowerCase())), [policy.data, filter]);
  const create = useMutation({
    mutationFn: () => post<ApiTokenRow & { token: string }>("/me/tokens", {
      name: name.trim(), expires_in_days: days, scopes: restrict ? scopes : null,
      allowed_cidrs: cidrs.trim() ? cidrs.split(/[\s,]+/).filter(Boolean) : null,
    }),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["my-tokens"] }); onCreated(r.token); setName(""); setScopes([]); setRestrict(false); setCidrs(""); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const valid = name.trim().length > 0 && (!restrict || scopes.length > 0);
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create API token" width="max-w-xl"
      description="The token acts as you, limited to the permissions you choose. It never exceeds your current access."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!valid || create.isPending} onClick={() => create.mutate()}>Create token</Button></>}>
      <div className="space-y-4">
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. grafana-inventory-sync" maxLength={128} /></Field>
        <Field label="Expires after" hint={`Required. At most ${policy.data?.max_days ?? 365} days on this installation.`}>
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {choices.map((d) => <option key={d} value={d}>{d} days</option>)}
          </Select>
        </Field>
        <fieldset className="space-y-2">
          <legend className="mb-1 text-[13px] font-medium">Permissions</legend>
          <label className="flex items-center gap-2 text-[13px]"><input type="radio" checked={!restrict} onChange={() => setRestrict(false)} /> Same as mine (follows changes to my roles)</label>
          <label className="flex items-center gap-2 text-[13px]"><input type="radio" checked={restrict} onChange={() => setRestrict(true)} /> Only selected permissions</label>
          {restrict && (
            <div className="rounded-md border border-border p-2">
              <Input aria-label="Filter permissions" placeholder="Filter, e.g. server or query" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-2 h-8" />
              {policy.isLoading ? <SkeletonRows rows={3} /> : (
                <div className="scroll-thin grid max-h-48 grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2">
                  {available.map((s) => (
                    <label key={s} className="flex items-center gap-2 font-mono text-[12px]">
                      <input type="checkbox" checked={scopes.includes(s)} onChange={(e) => setScopes((p) => e.target.checked ? [...p, s] : p.filter((x) => x !== s))} /> {s}
                    </label>
                  ))}
                </div>
              )}
              <div className="mt-1 text-[12px] text-muted">{scopes.length} selected. Only permissions you hold are offered.</div>
            </div>
          )}
        </fieldset>
        <Field label="Allowed source addresses (optional)" hint="CIDRs separated by commas or spaces, e.g. 10.20.0.0/16. Empty = any address.">
          <Input value={cidrs} onChange={(e) => setCidrs(e.target.value)} placeholder="10.20.0.0/16" />
        </Field>
      </div>
    </Dialog>
  );
}

function SecretOnce({ token, onClose }: { token: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(token); setCopied(true); } catch { toast.error("Copy failed — select the token and copy it manually"); }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Copy your new token" width="max-w-xl"
      description="This is the only time the token is shown. Store it in your secret manager now."
      footer={<Button variant="primary" onClick={onClose}>I have stored it</Button>}>
      <div className="flex items-center gap-2">
        <code data-testid="new-token" className="flex-1 select-all break-all rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[12px]">{token}</code>
        <Button onClick={copy} aria-label="Copy token">{copied ? <Check /> : <Copy />}</Button>
      </div>
      <p className="mt-3 text-[12.5px] text-muted">Use it as <code className="font-mono">Authorization: Bearer &lt;token&gt;</code>. Tokens cannot manage tokens, passwords, users, roles or identity providers.</p>
    </Dialog>
  );
}

export function ApiTokensPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["my-tokens"], queryFn: () => get<ApiTokenRow[]>("/me/tokens") });
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiTokenRow | null>(null);
  const revoke = useMutation({
    mutationFn: (t: ApiTokenRow) => del(`/me/tokens/${t.id}`),
    onSuccess: () => { toast.success("Token revoked"); setRevoking(null); qc.invalidateQueries({ queryKey: ["my-tokens"] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <div>
      <PageHeader title="API tokens" crumbs={[{ label: "Account" }, { label: "API tokens" }]}
        description="Personal tokens for scripts and integrations calling the REST API. They are stored hashed and shown once."
        actions={<Button variant="primary" onClick={() => setCreating(true)}><Plus /> Create token</Button>} />
      <Card>
        <CardHeader title={<span className="flex items-center gap-2"><KeyRound className="size-4" /> Your tokens</span>} />
        <CardBody className="p-0">
          {q.isLoading ? <div className="p-4"><SkeletonRows rows={3} /></div> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : <TokenTable tokens={q.data ?? []} onRevoke={setRevoking} />}
        </CardBody>
      </Card>
      <CreateTokenDialog open={creating} onOpenChange={setCreating} onCreated={(t) => { setCreating(false); setSecret(t); }} />
      {secret && <SecretOnce token={secret} onClose={() => setSecret(null)} />}
      <ConfirmDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)} title={`Revoke ${revoking?.name ?? ""}?`} danger
        confirmLabel="Revoke token" busy={revoke.isPending} onConfirm={() => revoking && revoke.mutate(revoking)}>
        Anything using this token stops working immediately. This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}

/** Administrator view inside Platform → Users. */
export function UserTokens({ userId }: { userId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["user-tokens", userId], queryFn: () => get<ApiTokenRow[]>(`/users/${userId}/tokens`) });
  const [revoking, setRevoking] = useState<ApiTokenRow | null>(null);
  const revoke = useMutation({
    mutationFn: (t: ApiTokenRow) => del(`/users/${userId}/tokens/${t.id}`),
    onSuccess: () => { toast.success("Token revoked"); setRevoking(null); qc.invalidateQueries({ queryKey: ["user-tokens", userId] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <div>
      <h4 className="mb-2 text-[13px] font-semibold">API tokens</h4>
      {q.isLoading ? <SkeletonRows rows={2} /> : q.error ? <ErrorState error={q.error} /> : <div className="rounded-md border border-border"><TokenTable tokens={q.data ?? []} onRevoke={setRevoking} /></div>}
      <ConfirmDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)} title={`Revoke ${revoking?.name ?? ""}?`} danger
        confirmLabel="Revoke token" busy={revoke.isPending} onConfirm={() => revoking && revoke.mutate(revoking)}>
        The owner's integrations using this token stop working immediately.
      </ConfirmDialog>
    </div>
  );
}

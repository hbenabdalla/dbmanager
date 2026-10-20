/** Post-setup checklist (GET /platform/setup/checklist): a compact dashboard card and a full page. */
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ChevronRight, Circle, ListChecks } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ChangePasswordDialog } from "@/features/auth/change-password";
import { get } from "@/api/client";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { cn } from "@/lib/utils";

export interface ChecklistItem { id: string; title: string; description: string; done: boolean; href: string | null; optional: boolean }
export interface Checklist { items: ChecklistItem[]; done: number; total: number }

export const useChecklist = (enabled = true) =>
  useQuery({ queryKey: ["setup-checklist"], queryFn: () => get<Checklist>("/platform/setup/checklist"), enabled, staleTime: 30_000 });

function Progress({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label="Setup progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <div className="h-full rounded-full bg-ok transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${pct}%` }} />
    </div>
  );
}

function Item({ it, compact, onAction }: { it: ChecklistItem; compact?: boolean; onAction?: (href: string) => void }) {
  const inner = (
    <>
      {it.done ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden /> : <Circle className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />}
      <span className="min-w-0 flex-1">
        <span className={cn("block text-[13px]", it.done ? "text-muted line-through decoration-muted/50" : "font-medium")}>
          {it.title}{it.optional && <span className="ml-1.5 text-2xs font-normal text-muted">optional</span>}
          <span className="sr-only">{it.done ? " (done)" : " (to do)"}</span>
        </span>
        {!compact && <span className="block text-[12.5px] text-muted">{it.description}</span>}
      </span>
      {!it.done && it.href && <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />}
    </>
  );
  const cls = "flex items-start gap-2.5 rounded-md px-2 py-1.5 transition-colors duration-150 motion-reduce:transition-none";
  if (!it.done && it.href?.startsWith("#")) {
    // in-page actions (e.g. "#change-password") open a dialog instead of navigating
    return <li><button type="button" onClick={() => onAction?.(it.href!)} className={cn(cls, "w-full text-left hover:bg-surface-3")}>{inner}</button></li>;
  }
  return <li>{!it.done && it.href ? <Link to={it.href} className={cn(cls, "hover:bg-surface-3")}>{inner}</Link> : <div className={cls}>{inner}</div>}</li>;
}

/** Compact dashboard card; hides itself once every required item is done. */
function useActions() {
  const [pw, setPw] = useState(false);
  const onAction = (href: string) => { if (href === "#change-password") setPw(true); };
  const dialog = pw ? <ChangePasswordDialog open onOpenChange={setPw} /> : null;
  return { onAction, dialog };
}

export function SetupChecklist({ className, hideWhenDone = true }: { className?: string; hideWhenDone?: boolean }) {
  const q = useChecklist();
  const { onAction, dialog } = useActions();
  if (q.isLoading) return <Card className={className}><SkeletonRows rows={4} /></Card>;
  if (q.error || !q.data) {
    return (
      <Card className={className}>
        <CardHeader title={<span className="flex items-center gap-2"><ListChecks className="size-4" aria-hidden /> Finish setting up</span>} />
        <ErrorState error={q.error} onRetry={() => q.refetch()} className="py-6" />
      </Card>
    );
  }
  const required = q.data.items.filter((i) => !i.optional);
  if (hideWhenDone && required.every((i) => i.done)) return null;
  const open = q.data.items.filter((i) => !i.done).slice(0, 5);
  return (
    <Card className={className}>
      <CardHeader title={<span className="flex items-center gap-2"><ListChecks className="size-4" aria-hidden /> Finish setting up</span>}
        description={`${q.data.done} of ${q.data.total} done`} actions={<Button size="sm" variant="ghost" asChild><Link to="/setup/checklist">All items</Link></Button>} />
      <CardBody className="space-y-2">
        <Progress done={q.data.done} total={q.data.total} />
        <ul className="-mx-2">{open.map((it) => <Item key={it.id} it={it} compact onAction={onAction} />)}</ul>
      </CardBody>
      {dialog}
    </Card>
  );
}

export function SetupChecklistPage() {
  const q = useChecklist();
  const { onAction, dialog } = useActions();
  return (
    <div>
      <PageHeader title="Setup checklist" crumbs={[{ label: "Platform" }, { label: "Setup checklist" }]}
        description="What is left to get the most out of DBManager. Items tick themselves off as the platform detects them."
        actions={<Button asChild><Link to="/setup">Open the setup wizard</Link></Button>}
        meta={q.data && <Badge tone={q.data.done === q.data.total ? "ok" : "neutral"}>{q.data.done} / {q.data.total}</Badge>} />
      {q.isLoading ? <SkeletonRows rows={8} /> : q.error || !q.data ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
        <Card><CardBody className="space-y-3"><Progress done={q.data.done} total={q.data.total} /><ul className="-mx-2 space-y-0.5">{q.data.items.map((it) => <Item key={it.id} it={it} onAction={onAction} />)}</ul></CardBody></Card>
      )}
      {dialog}
    </div>
  );
}

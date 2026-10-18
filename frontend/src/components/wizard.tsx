/** Reusable wizard primitives: an accessible stepper (step list + progress + back/next), a staged
 * check list (connection tests, directory tests) and a success panel. Motion is 150–300 ms and
 * disabled under prefers-reduced-motion (Tailwind `motion-reduce:`). */
import { Check, CheckCircle2, ChevronLeft, ChevronRight, Circle, CircleAlert, CircleMinus, Loader2, XCircle } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface WizardStep { id: string; title: string; description?: string; optional?: boolean }

export function Wizard({
  steps, current, completed, onStepChange, children, onBack, onNext, nextLabel = "Continue", nextDisabled, busy,
  hideNav, extraActions, className, title,
}: {
  steps: WizardStep[]; current: string; completed?: Iterable<string>; onStepChange?: (id: string) => void; children: ReactNode;
  onBack?: () => void; onNext?: () => void; nextLabel?: ReactNode; nextDisabled?: boolean; busy?: boolean; hideNav?: boolean;
  extraActions?: ReactNode; className?: string; title?: string;
}) {
  const done = new Set(completed ?? []);
  const idx = Math.max(0, steps.findIndex((s) => s.id === current));
  const step = steps[idx];
  const headingRef = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);
  useEffect(() => {
    // move focus to the new step's heading so keyboard and screen-reader users land in the content
    if (first.current) { first.current = false; return; }
    headingRef.current?.focus();
  }, [current]);
  const pct = steps.length > 1 ? Math.round((idx / (steps.length - 1)) * 100) : 100;
  // a step is reachable when it is done, current, or directly after a done one
  const reachable = (i: number) => i <= idx || done.has(steps[i].id) || (i > 0 && done.has(steps[i - 1].id));

  return (
    <div className={cn("grid gap-5 md:grid-cols-[220px_1fr]", className)}>
      <nav aria-label={title ? `${title} steps` : "Steps"} className="md:sticky md:top-4 md:self-start">
        <div className="mb-3 md:hidden">
          <div className="mb-1 flex justify-between text-[12px] text-muted"><span>Step {idx + 1} of {steps.length}</span><span>{step?.title}</span></div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Progress">
            <div className="h-full rounded-full bg-accent transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <ol className="hidden space-y-0.5 md:block">
          {steps.map((s, i) => {
            const isCur = i === idx;
            const isDone = done.has(s.id) && !isCur;
            const can = !!onStepChange && reachable(i) && !isCur;
            return (
              <li key={s.id}>
                <button type="button" disabled={!can} onClick={() => can && onStepChange?.(s.id)} aria-current={isCur ? "step" : undefined}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors duration-150 motion-reduce:transition-none",
                    isCur ? "bg-accent-soft font-medium text-accent" : can ? "text-text-2 hover:bg-surface-3" : "text-muted",
                  )}>
                  <span className={cn("grid size-5 shrink-0 place-items-center rounded-full border text-2xs tabular",
                    isCur ? "border-accent bg-accent text-accent-fg" : isDone ? "border-ok bg-ok text-white" : "border-border-strong")}>
                    {isDone ? <Check className="size-3" aria-hidden /> : i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{s.title}{s.optional && <span className="ml-1 text-2xs text-muted">optional</span>}</span>
                  {isDone && <span className="sr-only">(completed)</span>}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>
      <section aria-labelledby="wizard-step-title" className="min-w-0">
        <div key={step?.id} className="animate-[dbm-step-in_200ms_ease-out] motion-reduce:animate-none">
          <h2 id="wizard-step-title" ref={headingRef} tabIndex={-1} className="text-[17px] font-semibold outline-none">{step?.title}</h2>
          {step?.description && <p className="mt-1 text-[13px] text-muted">{step.description}</p>}
          <div className="mt-4">{children}</div>
        </div>
        {!hideNav && (
          <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-border pt-4">
            {onBack && idx > 0 && <Button type="button" variant="ghost" onClick={onBack} disabled={busy}><ChevronLeft /> Back</Button>}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {extraActions}
              {onNext && <Button type="button" variant="primary" onClick={onNext} disabled={nextDisabled || busy}>{busy ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : null}{nextLabel}{!busy && <ChevronRight />}</Button>}
            </div>
          </div>
        )}
      </section>
      <style>{"@keyframes dbm-step-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}"}</style>
    </div>
  );
}

export type StageStatus = "pending" | "running" | "ok" | "warn" | "fail" | "skipped";
export interface Stage { stage: string; label?: string; status: StageStatus; message?: string | null; duration_ms?: number | null; detail?: Record<string, unknown> | null }

const STAGE_ICON: Record<StageStatus, ReactNode> = {
  pending: <Circle className="size-4 text-muted" aria-hidden />,
  running: <Loader2 className="size-4 animate-spin text-accent motion-reduce:animate-none" aria-hidden />,
  ok: <CheckCircle2 className="size-4 text-ok" aria-hidden />,
  warn: <CircleAlert className="size-4 text-warn" aria-hidden />,
  fail: <XCircle className="size-4 text-crit" aria-hidden />,
  skipped: <CircleMinus className="size-4 text-muted" aria-hidden />,
};
const STATUS_TEXT: Record<StageStatus, string> = { pending: "waiting", running: "running", ok: "passed", warn: "warning", fail: "failed", skipped: "skipped" };

/** Staged check results; announced politely so screen readers follow progress. */
export function StageList({ stages, labels, className }: { stages: Stage[]; labels?: Record<string, string>; className?: string }) {
  return (
    <ol className={cn("divide-y divide-border rounded-md border border-border", className)} aria-live="polite" aria-label="Test stages">
      {stages.map((s) => (
        <li key={s.stage} data-status={s.status} className={cn("flex items-start gap-3 px-3 py-2 text-[13px] transition-colors duration-200 motion-reduce:transition-none",
          s.status === "fail" && "bg-crit-soft/50", s.status === "running" && "bg-accent-soft/40")}>
          <span className="mt-0.5">{STAGE_ICON[s.status]}</span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium">{s.label ?? labels?.[s.stage] ?? s.stage}</span>
              <span className="shrink-0 text-2xs text-muted tabular">
                <span className="sr-only">{STATUS_TEXT[s.status]}</span>
                {s.duration_ms != null && s.status !== "running" ? `${Math.round(s.duration_ms)} ms` : ""}
              </span>
            </div>
            {s.message && <p className={cn("mt-0.5 break-words text-[12.5px]", s.status === "fail" ? "text-crit" : "text-muted")}>{s.message}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function SuccessPanel({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div role="status" className="flex flex-col items-center px-6 py-10 text-center animate-[dbm-step-in_250ms_ease-out] motion-reduce:animate-none">
      <div className="mb-3 grid size-12 place-items-center rounded-full bg-ok-soft text-ok"><CheckCircle2 className="size-7" aria-hidden /></div>
      <p className="text-[16px] font-semibold">{title}</p>
      {children && <div className="mt-1 max-w-md text-[13px] text-muted">{children}</div>}
      {actions && <div className="mt-5 flex flex-wrap justify-center gap-2">{actions}</div>}
    </div>
  );
}

/** Small "Monitoring Only vs Administration Enabled" style choice card. */
export function ChoiceCard({ selected, onSelect, title, children, icon, badge, name }: {
  selected: boolean; onSelect: () => void; title: string; children?: ReactNode; icon?: ReactNode; badge?: ReactNode; name: string;
}) {
  return (
    <label className={cn("flex cursor-pointer gap-3 rounded-md border p-3 transition-colors duration-150 motion-reduce:transition-none",
      selected ? "border-accent bg-accent-soft/50 ring-1 ring-accent" : "border-border hover:border-border-strong hover:bg-surface-2")}>
      <input type="radio" name={name} className="mt-1" checked={selected} onChange={onSelect} />
      {icon && <span className="mt-0.5 text-muted [&_svg]:size-5">{icon}</span>}
      <span className="min-w-0">
        <span className="flex items-center gap-2 text-[13.5px] font-medium">{title}{badge}</span>
        {children && <span className="mt-0.5 block text-[12.5px] text-muted">{children}</span>}
      </span>
    </label>
  );
}

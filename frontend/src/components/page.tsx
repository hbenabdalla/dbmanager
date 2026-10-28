import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/utils";

export interface Crumb { label: string; to?: string }

export function PageHeader({ title, description, crumbs, actions, meta, className }: {
  title: ReactNode; description?: ReactNode; crumbs?: Crumb[]; actions?: ReactNode; meta?: ReactNode; className?: string;
}) {
  return (
    <header className={cn("mb-5", className)}>
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-2 flex items-center gap-1 text-[12.5px] text-muted">
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="size-3.5 opacity-60" />}
              {c.to ? <Link to={c.to} className="hover:text-text">{c.label}</Link> : <span className="text-text-2">{c.label}</span>}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[22px] font-bold leading-tight tracking-[-0.01em] text-heading">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-[13px] text-muted">{description}</p>}
          {meta && <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">{meta}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Stat({ label, value, sub, tone, className }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "ok" | "warn" | "crit" | "down"; className?: string }) {
  const color = tone ? `var(--${tone})` : undefined;
  return (
    <div className={cn("min-w-0 px-4 py-3", className)}>
      <div className="truncate text-[12px] text-muted">{label}</div>
      <div className="tabular mt-0.5 text-[22px] font-semibold leading-tight" style={{ color }}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-[12px] text-muted">{sub}</div>}
    </div>
  );
}

export function KeyValue({ items, className }: { items: [string, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,max-content)_1fr] gap-x-4 gap-y-1.5 text-[13px]", className)}>
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CodeBlock({ code, className, maxHeight = "28rem" }: { code: string; className?: string; maxHeight?: string }) {
  return (
    <pre className={cn("scroll-thin overflow-auto rounded-md border border-border bg-surface-2 p-3 font-mono text-[12.5px] leading-relaxed text-text-2", className)} style={{ maxHeight }}>
      {code}
    </pre>
  );
}

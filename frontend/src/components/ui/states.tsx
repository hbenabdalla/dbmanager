import { AlertTriangle, DatabaseZap, Inbox, Lock, PlugZap } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "@/api/client";
import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4", className)} />;
}

export function SkeletonRows({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2.5 p-4", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={cn("h-5", i % 3 === 2 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export function EmptyState({ icon, title, children, action, className }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center px-6 py-12 text-center", className)}>
      <div className="mb-3 rounded-full bg-surface-3 p-2.5 text-muted [&_svg]:size-5">{icon ?? <Inbox />}</div>
      <p className="font-medium text-text">{title}</p>
      {children && <div className="mt-1 max-w-md text-[12.5px] text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, className, onRetry }: { error: unknown; className?: string; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  if (e?.code === "timeseries_unavailable") return <StorageUnavailable className={className} onRetry={onRetry} />;
  const icon = e?.status === 403 ? <Lock /> : e?.code === "target_unavailable" ? <PlugZap /> : <AlertTriangle />;
  const title =
    e?.status === 403
      ? "You don't have access to this"
      : e?.code === "capability_not_supported"
        ? "Not available for this server"
        : e?.code === "target_unavailable"
          ? "The database server can't be reached"
          : "Something went wrong";
  return (
    <EmptyState icon={icon} title={title} className={className}
      action={onRetry && e?.status !== 403 && e?.code !== "capability_not_supported" ? <button className="text-[13px] font-medium text-accent hover:underline" onClick={onRetry}>Try again</button> : undefined}>
      <span>{e?.message ?? String((error as Error)?.message ?? error)}</span>
      {e?.requestId && <span className="mt-1 block font-mono text-2xs text-muted/80">Request {e.requestId}</span>}
    </EmptyState>
  );
}

/** Shown wherever charts read the time-series database while it is unreachable. */
export function StorageUnavailable({ className, onRetry, compact }: { className?: string; onRetry?: () => void; compact?: boolean }) {
  if (compact) {
    return (
      <div role="status" className={cn("flex items-center justify-center gap-2 text-[12.5px] text-muted", className)}>
        <DatabaseZap className="h-4 w-4" aria-hidden /> Monitoring Storage Unavailable
      </div>
    );
  }
  return (
    <EmptyState icon={<DatabaseZap />} title="Monitoring Storage Unavailable" className={className}
      action={onRetry ? <button className="text-[13px] font-medium text-accent hover:underline" onClick={onRetry}>Try again</button> : undefined}>
      <span>Metric history can't be read right now. Inventory, administration and security keep working; new samples are buffered and written when the storage is back.</span>
    </EmptyState>
  );
}

export function isStorageUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.code === "timeseries_unavailable";
}

/**
 * The DBManager logo, as provided (docs/assets/brand/dbmanager-logo-source.png): only scaled,
 * never redrawn or recoloured. Its navy lettering and ring need a light background, so on dark
 * surfaces (the sidebar, dark theme) it sits on a light plate rather than being altered.
 */
import logoUrl from "@/assets/brand/dbmanager/dbmanager-logo.png";
import logoSmallUrl from "@/assets/brand/dbmanager/dbmanager-logo-small.png";
import markUrl from "@/assets/brand/dbmanager/dbmanager-mark.png";
import { cn } from "@/lib/utils";

export const PRODUCT_NAME = "DBManager";

/** Symbol only (database, ring, cloud, chart) — for compact places: collapsed sidebar, tiles. */
export function BrandMark({ size = 28, plate, className, label }: { size?: number; plate?: boolean; className?: string; label?: string }) {
  const img = <img src={markUrl} alt={plate ? "" : (label ?? "")} width={size} height={Math.round(size * 0.76)} draggable={false}
    className={cn("block select-none object-contain", !plate && className)} style={{ width: size, height: "auto" }} />;
  if (!plate) return label ? <span role="img" aria-label={label} className="inline-flex">{img}</span> : img;
  return (
    <span role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}
      className={cn("inline-grid shrink-0 place-items-center rounded-[9px] bg-white shadow-[0_1px_0_rgba(255,255,255,0.08)_inset,0_1px_2px_rgba(0,0,0,0.25)] ring-1 ring-black/5", className)}
      style={{ width: size + 8, height: size + 8 }}>
      {img}
    </span>
  );
}

/** Full logo with the "DBManager" lettering — login, setup, About, loading, first run. */
export function BrandLogo({ width = 180, plate = "auto", className, decorative }: {
  width?: number;
  /** auto: a light plate in dark theme only; always: on every theme; never */
  plate?: "auto" | "always" | "never";
  className?: string;
  decorative?: boolean;
}) {
  const src = width <= 240 ? logoSmallUrl : logoUrl;
  return (
    <span className={cn(
      "inline-flex shrink-0 items-center justify-center",
      plate === "always" && "rounded-xl bg-white px-4 py-3 ring-1 ring-black/5",
      plate === "auto" && "dark:rounded-xl dark:bg-white dark:px-4 dark:py-3 dark:ring-1 dark:ring-white/10",
      className,
    )}>
      <img src={src} alt={decorative ? "" : PRODUCT_NAME} width={width} height={Math.round(width * 0.621)} draggable={false}
        className="block h-auto select-none" style={{ width }} />
    </span>
  );
}

/** Full-screen loading state with the logo (fades in; static under reduced motion). */
export function BrandLoading({ label = "Loading DBManager…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="grid min-h-[60vh] place-items-center">
      <div className="flex flex-col items-center gap-4 animate-[dbm-fade_400ms_ease-out_both] motion-reduce:animate-none">
        <BrandLogo width={150} decorative />
        <span className="flex items-center gap-2 text-[13px] text-muted">
          <span className="size-1.5 animate-pulse rounded-full bg-accent motion-reduce:animate-none" aria-hidden />{label}
        </span>
      </div>
      <style>{"@keyframes dbm-fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}"}</style>
    </div>
  );
}

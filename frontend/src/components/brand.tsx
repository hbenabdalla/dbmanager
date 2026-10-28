/**
 * KBM DBManager brand (docs/branding/kbm-dbmanager-brand-guidelines.md).
 *
 * The KBM logo is used exactly as supplied by KBM (frontend/src/assets/brand/kbm/*.svg, the same
 * files as docs/assets/brand/kbm): only scaled, never redrawn, recoloured or stretched. Blue on
 * light surfaces, white on KBM blue / navy. The *mark* variant is the supplied artwork without the
 * company-name lines (the variant KBM uses in its document footers).
 *
 * The product lockup is: [KBM mark] | DBManager — the official product name "KBM DBManager".
 */
import kbmLogoUrl from "@/assets/brand/kbm/kbm-logo.svg";
import kbmLogoWhiteUrl from "@/assets/brand/kbm/kbm-logo-white.svg";
import kbmMarkUrl from "@/assets/brand/kbm/kbm-mark.svg";
import kbmMarkWhiteUrl from "@/assets/brand/kbm/kbm-mark-white.svg";
import { cn } from "@/lib/utils";

/** Official, customer-facing product name. */
export const PRODUCT_NAME = "KBM DBManager";
/** The product part of the name, set next to the KBM mark in the lockup. */
export const PRODUCT_SHORT = "DBManager";
export const COMPANY_NAME = "Khorafi Business Machines (KBM)";
export const PRODUCT_TAGLINE = "Unified Database Operations";

/** aspect ratios of the supplied artwork (viewBox width / height) */
const MARK_RATIO = 491 / 163;
const LOGO_RATIO = 491 / 250;

type Tone = "auto" | "color" | "white";

/**
 * The KBM logo: `full` = with "Khorafi Business Machines (W.L.L.)" and the Arabic name,
 * otherwise the KBM mark only. `tone` auto = blue in the light theme, white in the dark theme.
 */
export function KbmLogo({ full, width = 120, tone = "auto", className, label = "KBM" }: {
  full?: boolean; width?: number; tone?: Tone; className?: string; label?: string;
}) {
  const ratio = full ? LOGO_RATIO : MARK_RATIO;
  const color = full ? kbmLogoUrl : kbmMarkUrl;
  const white = full ? kbmLogoWhiteUrl : kbmMarkWhiteUrl;
  const img = (src: string, extra?: string, alt = label) => (
    <img src={src} alt={alt} width={width} height={Math.round(width / ratio)} draggable={false}
      className={cn("block h-auto select-none object-contain", extra)} style={{ width }} />
  );
  if (tone === "color") return <span className={cn("inline-flex shrink-0", className)}>{img(color)}</span>;
  if (tone === "white") return <span className={cn("inline-flex shrink-0", className)}>{img(white)}</span>;
  return (
    <span className={cn("inline-flex shrink-0", className)}>
      {img(color, "dark:hidden")}
      {img(white, "hidden dark:block", "")}
    </span>
  );
}

/** Compact identity (collapsed sidebar, tiles): the KBM mark. */
export function BrandMark({ size = 28, tone = "white", className, label }: {
  size?: number; tone?: Tone; className?: string; label?: string;
}) {
  const mark = <KbmLogo width={Math.round(size * 1.45)} tone={tone} label="" className={className} />;
  return label ? <span role="img" aria-label={label} className="inline-flex">{mark}</span> : mark;
}

/**
 * The product lockup: KBM mark, a thin divider and "DBManager". `tone` white for KBM blue/navy
 * backgrounds; auto follows the theme.
 */
export function BrandLogo({ width = 180, tone = "auto", className, decorative, subtitle }: {
  width?: number; tone?: Tone; className?: string; decorative?: boolean; subtitle?: string;
}) {
  // proportions keep the whole lockup within about `width` pixels
  const markW = Math.round(width * 0.31);
  const text = Math.round(width * 0.112);
  const onBrand = tone === "white";
  return (
    <span role={decorative ? undefined : "img"} aria-label={decorative ? undefined : PRODUCT_NAME} aria-hidden={decorative || undefined}
      className={cn("inline-flex shrink-0 items-center", className)} style={{ gap: Math.round(width * 0.045) }}>
      <KbmLogo width={markW} tone={tone} label="" />
      <span aria-hidden className={cn("self-stretch border-l", onBrand ? "border-white/35" : "border-brand-line")} />
      <span aria-hidden className="flex flex-col leading-none">
        <span className={cn("font-bold tracking-[-0.01em]", onBrand ? "text-white" : "text-brand dark:text-text")} style={{ fontSize: text }}>
          {PRODUCT_SHORT}
        </span>
        {subtitle && (
          <span className={cn("mt-1 font-medium uppercase tracking-[0.12em]", onBrand ? "text-white/70" : "text-muted")} style={{ fontSize: Math.max(9, Math.round(text * 0.42)) }}>
            {subtitle}
          </span>
        )}
      </span>
    </span>
  );
}

/** Full-screen loading state with the product lockup (fades in; static under reduced motion). */
export function BrandLoading({ label = `Loading ${PRODUCT_NAME}…` }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="grid min-h-[60vh] place-items-center">
      <div className="flex flex-col items-center gap-4 animate-[dbm-fade_400ms_ease-out_both] motion-reduce:animate-none">
        <BrandLogo width={190} decorative />
        <span className="flex items-center gap-2 text-[13px] text-muted">
          <span className="size-1.5 animate-pulse rounded-full bg-accent motion-reduce:animate-none" aria-hidden />{label}
        </span>
      </div>
      <style>{"@keyframes dbm-fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}"}</style>
    </div>
  );
}

/**
 * KBM graphic motif from its covers: thin horizontal bands, a dot grid and ringed circles on KBM
 * blue. Decorative only; place inside a `relative` container with a brand background.
 */
export function KbmCoverMotif({ className, dots = true }: { className?: string; dots?: boolean }) {
  return (
    <svg aria-hidden className={cn("pointer-events-none absolute inset-0 h-full w-full", className)} preserveAspectRatio="xMidYMid slice" viewBox="0 0 770 900">
      <g fill="#ffffff">
        <rect x="0" y="268" width="770" height="9" opacity=".06" />
        <rect x="0" y="372" width="770" height="6" opacity=".06" />
        {dots && Array.from({ length: 11 }, (_, i) => (
          <circle key={i} cx={58 + (i % 4) * 34} cy={452 + Math.floor(i / 4) * 22} r="4" opacity=".2" />
        ))}
      </g>
      <g fill="none" stroke="#ffffff">
        <circle cx="618" cy="196" r="15" opacity=".28" />
        <circle cx="618" cy="196" r="8" fill="#ffffff" opacity=".12" />
        <circle cx="690" cy="240" r="27" opacity=".3" />
        <circle cx="690" cy="240" r="16" fill="#ffffff" opacity=".12" />
        <line x1="560" y1="88" x2="560" y2="252" opacity=".18" />
        <line x1="618" y1="72" x2="618" y2="178" opacity=".22" />
        <line x1="690" y1="60" x2="690" y2="210" opacity=".22" />
      </g>
    </svg>
  );
}

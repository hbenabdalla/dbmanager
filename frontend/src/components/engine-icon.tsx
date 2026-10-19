/**
 * DatabaseEngineIcon — the one component that shows which database engine something is.
 *
 * It draws DBManager's own engine marks (a tile in the engine colour with a monogram; Db2 for
 * z/OS carries a band that tells it apart from Db2 LUW; unknown engines get a neutral database
 * glyph). Vendor logos are not bundled: they are trademarks (see docs/development/brand-assets.md).
 * An organisation licensed to use them can place them in DBM_BRANDING_DIR; the backend then lists
 * them under /branding/engine-logos and this component shows them as images, unaltered, inside a
 * neutral plate — never recoloured or stretched.
 *
 * Accessibility: the engine name is the accessible name (role="img") or visible next to the mark
 * (withLabel), so colour never carries the meaning alone.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { get } from "@/api/client";
import { useOptionalAuth } from "@/lib/auth";
import { engineMeta } from "@/lib/engines";
import { cn } from "@/lib/utils";

export type EngineIconSize = "xs" | "sm" | "md" | "lg" | "xl";
const PX: Record<EngineIconSize, number> = { xs: 14, sm: 18, md: 24, lg: 32, xl: 48 };

/** Engines for which the operator supplied a licensed logo (empty unless DBM_BRANDING_DIR is set). */
export function useLicensedEngineLogos(): Set<string> {
  const auth = useOptionalAuth();
  const q = useQuery({
    queryKey: ["branding", "engine-logos"],
    queryFn: () => get<{ engines: string[] }>("/branding/engine-logos"),
    enabled: !!auth?.me,
    staleTime: Infinity,
    retry: false,
  });
  return new Set(q.data?.engines ?? []);
}

export function EngineMarkSvg({ engine, px, title }: { engine: string; px: number; title?: string }) {
  const m = engineMeta(engine);
  const unknown = m.family === "unknown";
  const zos = m.key === "db2zos";
  return (
    <svg width={px} height={px} viewBox="0 0 32 32" aria-hidden={title ? undefined : true} role={title ? "img" : undefined} focusable="false"
      className="shrink-0" data-engine-mark={m.key}>
      {title && <title>{title}</title>}
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5" fill={m.hue} />
      {/* soft top light, the same on every mark */}
      <path d="M8 .5h16A7.5 7.5 0 0 1 31.5 8v3.5H.5V8A7.5 7.5 0 0 1 8 .5Z" fill="#fff" opacity=".1" />
      {zos && <path d="M.5 23.5h31V24a7.5 7.5 0 0 1-7.5 7.5H8A7.5 7.5 0 0 1 .5 24Z" fill="#0b1f1d" opacity=".55" />}
      {zos && <rect x="6" y="26.2" width="20" height="1.6" rx=".8" fill="#fff" opacity=".75" />}
      {unknown ? (
        <g fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round">
          <ellipse cx="16" cy="10.5" rx="7" ry="2.8" />
          <path d="M9 10.5v11c0 1.6 3.1 2.8 7 2.8s7-1.2 7-2.8v-11" />
          <path d="M9 16c0 1.6 3.1 2.8 7 2.8s7-1.2 7-2.8" opacity=".7" />
        </g>
      ) : (
        <text x="16" y={zos ? 18.6 : 20.6} textAnchor="middle" fill="#fff" fontSize={m.short.length > 2 ? 11 : 13.5} fontWeight="700"
          fontFamily="'IBM Plex Sans', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif" letterSpacing="-0.2">
          {m.short}
        </text>
      )}
      {/* separates the tile from dark surfaces */}
      <rect x="0.5" y="0.5" width="31" height="31" rx="7.5" fill="none" className="stroke-black/10 dark:stroke-white/20" strokeWidth="1" />
    </svg>
  );
}

export function DatabaseEngineIcon({ engine, size = "sm", withLabel, decorative, version, className, labelClassName }: {
  engine: string | null | undefined;
  size?: EngineIconSize;
  /** show the engine name next to the mark (otherwise it is the accessible name only) */
  withLabel?: boolean;
  /** the engine is already named next to the icon: hide the icon from assistive technology */
  decorative?: boolean;
  /** detected version, appended to the label (from discovery — never hard-coded) */
  version?: string | null;
  className?: string;
  labelClassName?: string;
}) {
  const m = engineMeta(engine);
  const licensed = useLicensedEngineLogos();
  const [broken, setBroken] = useState(false);
  const px = PX[size];
  const name = version ? `${m.label} ${version}` : m.label;
  const useLogo = licensed.has(m.key) && !broken;
  const mark = useLogo ? (
    <span aria-hidden className="grid shrink-0 place-items-center rounded-[22%] bg-white ring-1 ring-black/10" style={{ width: px, height: px }}>
      <img src={`/api/v1/branding/engine-logos/${m.key}`} alt="" onError={() => setBroken(true)} draggable={false}
        className="max-h-[80%] max-w-[80%] object-contain" />
    </span>
  ) : <EngineMarkSvg engine={m.key} px={px} />;
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5 align-middle", className)} data-engine={m.key}
      {...(withLabel ? {} : decorative ? { "aria-hidden": true } : { role: "img", "aria-label": name, title: name })}>
      {mark}
      {withLabel && <span className={cn("truncate", labelClassName)}>{name}</span>}
    </span>
  );
}

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return digits === 2 ? nf2.format(v) : nf.format(v);
}

export function compactNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return Math.abs(v) < 10000 ? nf.format(v) : compact.format(v);
}

export function bytes(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let n = Math.abs(v);
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${v < 0 ? "-" : ""}${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(n < 10 ? 2 : 1)} ${units[i]}`;
}

export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v < 1) return `${v.toFixed(2)} ms`;
  if (v < 1000) return `${v.toFixed(v < 10 ? 1 : 0)} ms`;
  if (v < 60000) return `${(v / 1000).toFixed(1)} s`;
  return duration(v / 1000);
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v.toFixed(digits)}%`;
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
export function relTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function timeLabel(iso: string, spanSeconds: number): string {
  const d = new Date(iso);
  if (spanSeconds > 2 * 86400) return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function metricValue(unit: string | undefined, v: number | null | undefined): string {
  switch (unit) {
    case "B":
      return bytes(v);
    case "B/s":
      return v === null || v === undefined ? "—" : `${bytes(v)}/s`;
    case "%":
      return pct(v);
    case "ms":
      return ms(v);
    case "s":
      return duration(v);
    case "1/s":
      return v === null || v === undefined ? "—" : `${compactNum(v)}/s`;
    default:
      return compactNum(v);
  }
}

/** "30 second", "5 minute", "6 hour", "1 day" — the step of a downsampled chart. */
export function formatStep(seconds: number): string {
  if (seconds < 60) return `${seconds} second`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} minute`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} hour`;
  return `${Math.round(seconds / 86400)} day`;
}

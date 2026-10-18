import { RefreshCw } from "lucide-react";
import { REFRESH_OPTIONS, setRefreshMs, useRefreshMs } from "@/lib/refresh";
import { Select } from "./ui/input";

export function AutoRefreshPicker() {
  const ms = useRefreshMs();
  return (
    <label className="inline-flex items-center gap-1.5 text-[12.5px] text-muted">
      <RefreshCw className="h-3.5 w-3.5" aria-hidden />
      <span className="sr-only sm:not-sr-only">Auto-refresh</span>
      <Select aria-label="Auto-refresh" className="h-8 w-[76px]" value={String(ms)} onChange={(e) => setRefreshMs(Number(e.target.value))}>
        {REFRESH_OPTIONS.map((o) => <option key={o.ms} value={o.ms}>{o.label}</option>)}
      </Select>
    </label>
  );
}

export const AGGREGATIONS: [string, string][] = [
  ["avg", "Average"], ["min", "Minimum"], ["max", "Maximum"], ["p50", "Median (p50)"], ["p95", "p95"], ["p99", "p99"],
  ["sum", "Sum"], ["count", "Sample count"],
];

export function AggregationPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Select aria-label="Aggregation per point" className="h-8 w-[140px]" value={value} onChange={(e) => onChange(e.target.value)}>
      {AGGREGATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </Select>
  );
}
